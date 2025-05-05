# Remove Poppler-related imports and path setup
import sys
import os
import subprocess
import time
import gc  # For explicit garbage collection
import multiprocessing  # For CPU count detection
import io  # For BytesIO

# At the top of the file, add QIcon import if not already there
from PyQt6.QtWidgets import (
    QApplication, QWidget, QPushButton, QVBoxLayout, QFileDialog, QLabel, QComboBox, QLineEdit, QMessageBox, QDialog, QProgressBar,
    QHBoxLayout, QFrame, QGraphicsDropShadowEffect, QStackedWidget
)
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QSize, QTimer, QPoint
from PIL import Image, ImageFile  # Added ImageFile import here
from psd_tools import PSDImage

# Try to import PDF-specific libraries
try:
    import fitz  # PyMuPDF
    PDF_CONVERTER = "pymupdf"
except ImportError:
    PDF_CONVERTER = None

# Update SUPPORTED_FORMATS to include PDF
SUPPORTED_FORMATS = ["PNG", "JPEG", "BMP", "GIF", "TIFF", "WEBP", "PDF"]

class ConverterThread(QThread):
    progress_signal = pyqtSignal(int, str, str)
    completion_signal = pyqtSignal(str, float, float, int, int)
    error_signal = pyqtSignal(str, str)
    
    def __init__(self, files, output_dir, format_, psd_resolution="4K"):
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.format_ = format_
        self._is_running = True
        self.total_size = self._calculate_total_size()
        self.processed_size = 0
        self.start_time = 0
        # Define chunk size for large file processing (16MB)
        self.chunk_size = 16 * 1024 * 1024
        # Determine optimal number of threads based on CPU cores
        self.num_cores = max(1, multiprocessing.cpu_count() - 1)
        # Track conversion success/failure
        self.success_count = 0
        self.failure_count = 0
        # Add skip list for error handling
        self.skip_errors = []
        # Add skip_all flag to skip all errors
        self.skip_all = False
        # PSD resolution setting
        self.psd_resolution = psd_resolution

    def stop(self):
        self._is_running = False
    
    def _calculate_total_size(self):
        total = 0
        for file in self.files:
            try:
                total += os.path.getsize(file)
            except (OSError, FileNotFoundError):
                pass
        return total
    
    def run(self):
        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir)
            except Exception as e:
                self.error_signal.emit(f"Failed to create output directory: {str(e)}", "DIR_ERROR")
                return
    
        total_files = len(self.files)
        last_output_path = ""
        self.start_time = time.time()
        output_total_size = 0
        
        # Pre-sort files by size (process smaller files first for better user experience)
        try:
            self.files.sort(key=lambda f: os.path.getsize(f) if os.path.exists(f) else 0)
        except:
            pass  # If sorting fails, continue with original order
        
        for i, file in enumerate(self.files):
            if not self._is_running:
                break
                
            if not os.path.exists(file):
                # Check skip_all before emitting any error
                if self.skip_all:
                    self.failure_count += 1
                    self.processed_size += 0  # File doesn't exist, so size is 0
                    progress = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else int(((i + 1) / total_files) * 100)
                    self.progress_signal.emit(progress, "Skipping...", "")
                    continue
                else:
                    self.error_signal.emit(f"File not found: {file}", "FILE_NOT_FOUND")
                    self.failure_count += 1
                    continue
            
            name, ext = os.path.splitext(os.path.basename(file))
            ext = ext[1:].upper()
            
            # Check if this error type should be skipped or if skip_all is enabled
            if ext in self.skip_errors or self.skip_all:
                self.processed_size += os.path.getsize(file)
                progress = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else int(((i + 1) / total_files) * 100)
                self.progress_signal.emit(progress, "Skipping...", "")
                self.failure_count += 1
                continue
            
            # Modified to include PSD files as valid input
            if ext not in SUPPORTED_FORMATS and ext != "PSD":
                continue
            
            # Get file size before processing
            try:
                file_size = os.path.getsize(file)
            except (OSError, FileNotFoundError):
                file_size = 0
            
            output_path = os.path.join(self.output_dir, f"{name}.{self.format_.lower()}")
            last_output_path = output_path
            
            try:
                # Process file based on type and size
                if ext == "PSD":
                    # For PSD files, use psd_tools with memory management and enhanced quality
                    psd = PSDImage.open(file)
                    
                    # Use higher quality composite rendering for PSD files
                    # Fixed: Ensure proper color mode handling
                    image = psd.composite()
                    
                    # Apply resolution settings for all formats, not just PDF
                    # Get original PSD dimensions
                    width, height = psd.width, psd.height
                    
                    # Set target dimensions based on selected resolution
                    if self.psd_resolution == "HD":
                        target_width, target_height = 1280, 720
                        target_dpi = 300
                    elif self.psd_resolution == "FHD":
                        target_width, target_height = 1920, 1080
                        target_dpi = 600
                    else:  # 4K
                        target_width, target_height = 3840, 2160
                        target_dpi = 1200
                    
                    # Calculate aspect ratio of original image
                    aspect_ratio = width / height
                    
                    # Determine new dimensions while preserving aspect ratio
                    if aspect_ratio > (target_width / target_height):  # Wider than target
                        new_width = target_width
                        new_height = int(new_width / aspect_ratio)
                    else:  # Taller than target
                        new_height = target_height
                        new_width = int(new_height * aspect_ratio)
                    
                    # Resize image to target resolution
                    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    
                    # Clear any references to the original PSD to free memory
                    psd = None
                    gc.collect()  # Force garbage collection
                
                elif ext == "PDF":
                    # Unified PDF handling using PyMuPDF for all PDF files
                    if PDF_CONVERTER == "pymupdf":
                        pdf_document = fitz.open(file)
                        first_page = pdf_document[0]
                        # Ultra high quality zoom factor (16x) for 4K-comparable quality
                        zoom = 16
                        mat = fitz.Matrix(zoom, zoom)
                        pix = first_page.get_pixmap(matrix=mat, alpha=True, colorspace="rgb")
                        
                        # Convert to PIL Image with proper alpha channel
                        img_data = pix.tobytes("png")
                        image = Image.open(io.BytesIO(img_data))
                        
                        # Clean up
                        pdf_document.close()
                        pix = None
                        gc.collect()
                    else:
                        raise ImportError("PDF conversion requires PyMuPDF. Please install with: pip install PyMuPDF")
                
                else:
                    # For other image files, use PIL with memory optimization for large files
                    if file_size > 100 * 1024 * 1024:
                        with Image.open(file) as img:
                            # For large images, maintain 4K quality (3840×2160 minimum)
                            min_width = 3840
                            min_height = 2160
                            
                            # Calculate scaling to maintain aspect ratio but ensure 4K minimum dimensions
                            width, height = img.size
                            scale_w = max(1.0, min_width / width)
                            scale_h = max(1.0, min_height / height)
                            scale = max(scale_w, scale_h)
                            
                            # Apply scaling if needed
                            if scale > 1.0:
                                new_width = int(width * scale)
                                new_height = int(height * scale)
                                image = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                            else:
                                image = img.copy()
                    else:
                        # For smaller files, we can load directly and resize if needed
                        img = Image.open(file)
                        width, height = img.size
                        
                        # Ensure 4K minimum dimensions
                        min_width = 3840
                        min_height = 2160
                        
                        scale_w = max(1.0, min_width / width)
                        scale_h = max(1.0, min_height / height)
                        scale = max(scale_w, scale_h)
                        
                        if scale > 1.0:
                            new_width = int(width * scale)
                            new_height = int(height * scale)
                            image = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                        else:
                            image = img
                
                # Ensure proper color mode for the target format with high bit depth
                if self.format_ in ["JPG", "JPEG"]:
                    image = image.convert("RGB")
                elif self.format_ in ["PNG", "WEBP"] and image.mode == "P":
                    image = image.convert("RGBA")
                
                # Optimize saving based on format with 4K-quality settings
                if self.format_ == "PDF":
                    # Save as PDF using PIL with ultra-high DPI
                    if image.mode != 'RGB':
                        image = image.convert('RGB')
                    
                    # Enhanced PDF quality settings, especially for PSD source files
                    if ext == "PSD":
                        # Use resolution based on selected quality
                        if self.psd_resolution == "HD":
                            image.save(output_path, "PDF", resolution=300, save_all=True, quality=95)
                        elif self.psd_resolution == "FHD":
                            image.save(output_path, "PDF", resolution=600, save_all=True, quality=100)
                        else:  # 4K
                            image.save(output_path, "PDF", resolution=1200, save_all=True, quality=100)
                    else:
                        # For other source formats, use standard high quality
                        image.save(output_path, "PDF", resolution=1200, save_all=True)
                elif self.format_ in ["PNG"]:
                    # For PNG, use maximum quality (no compression)
                    image.save(output_path, self.format_, optimize=True, 
                              compress_level=0)  # 0 is no compression for highest quality
                elif self.format_ in ["WEBP"]:
                    # For WEBP, use lossless compression for highest quality
                    image.save(output_path, self.format_, lossless=True, quality=100)
                elif self.format_ in ["JPEG", "JPG"]:
                    # For JPEG, use highest quality setting with no subsampling
                    image.save(output_path, "JPEG", quality=100, optimize=True,
                              progressive=True, subsampling=0)  # subsampling=0 for highest quality
                elif self.format_ in ["TIFF"]:
                    # For TIFF, use lossless compression with high quality
                    image.save(output_path, self.format_, compression="tiff_deflate", quality=100)
                elif self.format_ in ["BMP"]:
                    # For BMP, use highest quality
                    image.save(output_path, self.format_)
                else:
                    # For other formats, use highest quality settings
                    image.save(output_path, self.format_, quality=100)
                
                # Increment success counter after successful save
                self.success_count += 1
                
                # Explicitly delete the image to free memory
                del image
                gc.collect()  # Force garbage collection after each large file
                
            except MemoryError:
                # Check skip_all before emitting any error
                if self.skip_all:
                    self.failure_count += 1
                    self.processed_size += file_size
                    progress = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else int(((i + 1) / total_files) * 100)
                    self.progress_signal.emit(progress, "Skipping...", "")
                    continue
                else:
                    self.error_signal.emit(f"Not enough memory to process {os.path.basename(file)}. Try closing other applications.", "MEMORY")
                    self.failure_count += 1
                    continue
            except ImportError as ie:
                # Special handling for import errors (missing libraries)
                self.error_signal.emit(f"Missing library: {str(ie)}", "IMPORT_ERROR")
                self.failure_count += 1
                continue
            except Exception as e:
                # Check skip_all before emitting any error
                if self.skip_all:
                    self.failure_count += 1
                    self.processed_size += file_size
                    progress = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else int(((i + 1) / total_files) * 100)
                    self.progress_signal.emit(progress, "Skipping...", "")
                    continue
                else:
                    error_type = ext  # Use file extension as error type
                    self.error_signal.emit(f"Error processing {os.path.basename(file)}: {str(e)}", error_type)
                    self.failure_count += 1
                    continue
            
            # Track output file size
            try:
                output_total_size += os.path.getsize(output_path)
            except (OSError, FileNotFoundError):
                pass
            
            # Update processed size
            self.processed_size += file_size
            
            # Calculate progress percentage
            progress = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else int(((i + 1) / total_files) * 100)
            
            # Calculate ETA with smoothing for more stable estimates
            elapsed_time = time.time() - self.start_time
            if elapsed_time > 0 and self.processed_size > 0:
                mb_per_sec = (self.processed_size / 1024 / 1024) / elapsed_time
                
                if progress > 0:
                    # Apply smoothing to ETA calculation
                    total_time_estimate = elapsed_time * (100 / progress)
                    remaining_seconds = total_time_estimate - elapsed_time
                    
                    # Format ETA
                    if remaining_seconds < 60:
                        eta_text = f"ETA: {int(remaining_seconds)}s"
                    else:
                        eta_text = f"ETA: {int(remaining_seconds // 60)}m {int(remaining_seconds % 60)}s"
                else:
                    eta_text = "ETA: Calculating..."
                
                # Format MB/s
                speed_text = f"Speed: {mb_per_sec:.2f} MB/s"
            else:
                eta_text = "ETA: Calculating..."
                speed_text = "Speed: Calculating..."
            
            self.progress_signal.emit(progress, eta_text, speed_text)
        
        # Final cleanup
        gc.collect()
        self.completion_signal.emit(last_output_path, self.total_size, output_total_size, 
                                   self.success_count, self.failure_count)

# Add modern color scheme
COLORS = {
    'primary': "#007AFF",
    'secondary': "#5856D6",
    'background_light': "#FFFFFF",
    'background_dark': "#1C1C1E",
    'surface_light': "#F2F2F7",
    'surface_dark': "#2C2C2E",
    'text_light': "#000000",
    'text_dark': "#FFFFFF",
    'border_light': "#E5E5EA",
    'border_dark': "#38383A",
    'success': "#34C759"
}

class AnimatedComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Hover animation
        self._animation = QPropertyAnimation(self, b"size")
        self._animation.setDuration(100)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)
        
    def enterEvent(self, event):
        self._animation.setStartValue(self.size())
        self._animation.setEndValue(QSize(self.width(), self.height() + 2))
        self._animation.start()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self._animation.setStartValue(self.size())
        self._animation.setEndValue(QSize(self.width(), self.height() - 2))
        self._animation.start()
        super().leaveEvent(event)

class ImageConverter(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Converter")
        self.setGeometry(100, 100, 500, 400)
        
        # Set window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Get system theme
        self.is_dark_mode = self.get_system_theme()
        self.apply_theme()
        
        # Initialize variables
        self.files = []
        self.output_dir = ""
        self.thread = None
        self.progress_dialog = None
        self.conversion_history = []
        
        self.initUI()

    def get_system_theme(self):
        # Get system color scheme
        app = QApplication.instance()
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def apply_theme(self):
        bg_color = COLORS['background_dark'] if self.is_dark_mode else COLORS['background_light']
        text_color = COLORS['text_dark'] if self.is_dark_mode else COLORS['text_light']
        border_color = COLORS['border_dark'] if self.is_dark_mode else COLORS['border_light']
        
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {bg_color};
                color: {text_color};
                font-family: 'Segoe UI', 'SF Pro Display', sans-serif;
            }}
            QPushButton {{
                background-color: {COLORS['primary']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 15px;  /* Adjusted padding to be more balanced */
                font-weight: 600;
                font-size: 13px;
                min-width: 120px;    /* Added minimum width */
            }}
            QPushButton:hover {{
                background-color: {COLORS['secondary']};
            }}
            QComboBox, QLineEdit {{
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 8px 12px;
                background: {COLORS['surface_dark'] if self.is_dark_mode else COLORS['surface_light']};
                min-height: 20px;
                font-size: 13px;
            }}
            QComboBox::drop-down {{
                border: none;
                padding-right: 10px;
            }}
            QLabel {{
                font-size: 13px;
                font-weight: 500;
            }}
            QProgressBar {{
                border: 1px solid {border_color};
                border-radius: 8px;
                text-align: center;
                background-color: {COLORS['surface_dark'] if self.is_dark_mode else COLORS['surface_light']};
                height: 8px;
            }}
            QProgressBar::chunk {{
                background-color: {COLORS['primary']};
                border-radius: 7px;
            }}
        """)

    def initUI(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)  # Increased spacing for more breathing room
        layout.setContentsMargins(30, 30, 30, 30)  # Wider margins for a more spacious feel
        
        # Define font
        font = QFont("Segoe UI", 10)
        
        # Create a header section with icon
        header_layout = QHBoxLayout()
        title_label = QLabel("Image Converter")
        title_label.setStyleSheet("font-size: 28px; font-weight: 600;")  # Larger, lighter font
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Add a subtle separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"background-color: {COLORS['border_dark'] if self.is_dark_mode else COLORS['border_light']}; max-height: 1px;")
        layout.addWidget(separator)
        layout.addSpacing(10)
        
        # Selection type with label
        type_layout = QHBoxLayout()
        type_label = QLabel("Selection Type:")
        type_label.setFixedWidth(120)
        type_layout.addWidget(type_label)
        
        self.selection_type_combo = AnimatedComboBox()
        self.selection_type_combo.addItems(["Select Folder", "Select Files"])  # Changed order here
        type_layout.addWidget(self.selection_type_combo)
        layout.addLayout(type_layout)
        
        # Input file selection with horizontal layout
        input_layout = QHBoxLayout()
        self.select_btn = QPushButton("Browse")
        self.select_btn.setFixedWidth(120)
        self.select_btn.setFont(font)
        # Add icon to Browse button
        self.select_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DialogOpenButton))
        self.select_btn.setIconSize(QSize(16, 16))
        self.select_btn.clicked.connect(self.select_files_or_folder)
        input_layout.addWidget(self.select_btn)
        
        self.file_label = QLineEdit()
        self.file_label.setFont(font)
        self.file_label.setPlaceholderText("Selected file(s) or folder will appear here")
        self.file_label.setReadOnly(True)
        input_layout.addWidget(self.file_label)
        layout.addLayout(input_layout)
        
        # Output folder selection with horizontal layout
        output_layout = QHBoxLayout()
        self.output_dir_btn = QPushButton("Output Folder")
        self.output_dir_btn.setFixedWidth(120)
        self.output_dir_btn.setFont(font)
        # Add icon to Output Folder button
        self.output_dir_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        self.output_dir_btn.setIconSize(QSize(16, 16))
        self.output_dir_btn.clicked.connect(self.select_output_folder)
        output_layout.addWidget(self.output_dir_btn)
        
        self.output_dir_label = QLineEdit()
        self.output_dir_label.setFont(font)
        self.output_dir_label.setPlaceholderText("Selected output folder will appear here")
        self.output_dir_label.setReadOnly(True)
        output_layout.addWidget(self.output_dir_label)
        layout.addLayout(output_layout)
        
        # Format selection with label
        format_layout = QHBoxLayout()
        format_label = QLabel("Output Format:")
        format_label.setFixedWidth(120)
        format_layout.addWidget(format_label)
        
        self.format_combo = AnimatedComboBox()  # Use animated combo box
        self.format_combo.addItems(SUPPORTED_FORMATS)
        self.format_combo.currentTextChanged.connect(self.on_format_changed)
        format_layout.addWidget(self.format_combo)
        layout.addLayout(format_layout)
        
        # PSD Resolution options (initially hidden)
        self.resolution_layout = QHBoxLayout()
        resolution_label = QLabel("PSD Resolution:")
        resolution_label.setFixedWidth(120)
        self.resolution_layout.addWidget(resolution_label)
        
        self.resolution_combo = AnimatedComboBox()
        self.resolution_combo.addItems(["HD (1280x720)", "FHD (1920x1080)", "4K (3840x2160)"])
        self.resolution_combo.setCurrentText("4K (3840x2160)")
        self.resolution_layout.addWidget(self.resolution_combo)
        layout.addLayout(self.resolution_layout)
        
        # Initially hide resolution options
        self.toggle_resolution_visibility(False)
        
        layout.addStretch()  # Push convert button to bottom
        
        # Convert button with shadow and animation
        self.convert_btn = QPushButton("Convert")
        self.convert_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        # Increase button height for better visibility
        self.convert_btn.setFixedHeight(55)  # Increased from 50 to 55
        # Add icon to Convert button
        self.convert_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ArrowRight))
        self.convert_btn.setIconSize(QSize(20, 20))
        self.convert_btn.clicked.connect(self.toggle_conversion)
        
        # Add shadow to convert button
        convert_shadow = QGraphicsDropShadowEffect()
        convert_shadow.setBlurRadius(15)
        convert_shadow.setColor(QColor(0, 0, 0, 80))
        convert_shadow.setOffset(0, 4)
        self.convert_btn.setGraphicsEffect(convert_shadow)
        
        layout.addWidget(self.convert_btn)
        
        self.setLayout(layout)
        
        self.files = []
        self.output_dir = ""
        self.thread = None
        self.progress_dialog = None
    
    def toggle_resolution_visibility(self, visible):
        """Helper method to show/hide resolution options"""
        self.resolution_layout.setEnabled(visible)
        for i in range(self.resolution_layout.count()):
            widget = self.resolution_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(visible)
    
    def on_format_changed(self, format_text):
        """Show resolution options when PSD files are present for any output format"""
        has_psd_files = any(os.path.splitext(f)[1].lower() == '.psd' for f in self.files) if self.files else False
        # Modified to show resolution options for any format when PSD files are selected
        show_resolution = has_psd_files
        self.toggle_resolution_visibility(show_resolution)
    
    def select_files_or_folder(self):
        if self.selection_type_combo.currentText() == "Select Files":
            files, _ = QFileDialog.getOpenFileNames(self, "Select Images", "", "Image Files (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp *.pdf *.psd)")
            if files:
                self.files = files
                display_text = os.path.basename(files[0]) if len(files) == 1 else f"{os.path.basename(files[0])} ... ({len(files)} files)"
                self.file_label.setText(display_text)
                
                # Check if any PSD files are selected and update resolution visibility
                has_psd = any(os.path.splitext(f)[1].lower() == '.psd' for f in files)
                # Modified to show resolution options for any format when PSD files are selected
                self.toggle_resolution_visibility(has_psd)
        else:
            folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing Images")
            if folder:
                # Improved file extension checking
                supported_files = []
                for f in os.listdir(folder):
                    file_path = os.path.join(folder, f)
                    if os.path.isfile(file_path):
                        ext = os.path.splitext(f)[1].lstrip('.').upper()
                        if ext in [fmt.upper() for fmt in SUPPORTED_FORMATS] or ext == "PSD":
                            supported_files.append(file_path)
                
                if supported_files:
                    self.files = supported_files
                    self.file_label.setText(f"{folder} ({len(supported_files)} files)")
                    
                    # Check for PSD files in folder selection
                    has_psd = any(os.path.splitext(f)[1].lower() == '.psd' for f in supported_files)
                    # Modified to show resolution options for any format when PSD files are selected
                    self.toggle_resolution_visibility(has_psd)
                else:
                    QMessageBox.warning(self, "Warning", "No supported image files found in the selected folder!")
                    self.files = []
                    self.file_label.clear()
    
    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_dir = folder
            self.output_dir_label.setText(folder)

    def toggle_conversion(self):
        if self.convert_btn.text() == "Convert":
            if not self.files or not self.output_dir:
                QMessageBox.warning(self, "Error", "Select files/folder and output folder!")
                return
            
            # Validate output directory
            if not os.path.exists(self.output_dir):
                try:
                    os.makedirs(self.output_dir)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Could not create output directory: {str(e)}")
                    return
            
            # Reset error_shown flag when starting a new conversion
            if hasattr(self, 'error_shown'):
                delattr(self, 'error_shown')
                
            self.progress_dialog = ProgressDialog(self)
            # Change button text to "Converting..." instead of "Cancel"
            self.convert_btn.setText("Converting...")
            
            # Get selected resolution for PSD files
            resolution_text = self.resolution_combo.currentText() if hasattr(self, 'resolution_combo') else "4K (3840x2160)"
            if "HD" in resolution_text:
                psd_resolution = "HD"
            elif "FHD" in resolution_text:
                psd_resolution = "FHD"
            else:
                psd_resolution = "4K"
            
            self.thread = ConverterThread(self.files, self.output_dir, self.format_combo.currentText(), psd_resolution)
            self.thread.progress_signal.connect(
                lambda value, eta, speed: self.progress_dialog.update_progress(value, eta, speed)
            )
            self.thread.completion_signal.connect(
                lambda path, in_size, out_size, success, failure: 
                self.on_conversion_complete(path, in_size, out_size, success, failure)
            )
            self.thread.error_signal.connect(self.handle_conversion_error)
            
            self.thread.start()
            self.progress_dialog.exec()
        else:
            # Cancel conversion
            if self.thread:
                self.thread.stop()
            if self.progress_dialog:
                self.progress_dialog.reject()
            self.convert_btn.setText("Convert")
            # Remove duplicate code here

    def handle_conversion_error(self, error_message, error_type):
        # Skip showing error dialog if we've already shown one
        if not hasattr(self, 'error_shown'):
            self.error_shown = True
            
            # Create a custom dialog with simplified options
            error_dialog = QMessageBox(self)
            error_dialog.setWindowTitle("Conversion Error")
            error_dialog.setText("Some files could not be converted. Processing will continue with the remaining files.")
            error_dialog.setDetailedText(error_message)
            error_dialog.setIcon(QMessageBox.Icon.Warning)
            
            # Add buttons with clear labels
            skip_button = error_dialog.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
            retry_button = error_dialog.addButton("Retry All", QMessageBox.ButtonRole.RejectRole)
            
            result = error_dialog.exec()
            
            clicked_button = error_dialog.clickedButton()
            
            # Check if dialog was closed with X button (no clicked button)
            if clicked_button is None:
                # Cancel the conversion when X is clicked
                if self.thread:
                    self.thread.stop()
                if self.progress_dialog:
                    self.progress_dialog.reject()
                self.convert_btn.setText("Convert")
                # Reset error_shown flag
                delattr(self, 'error_shown')
                return
            
            if clicked_button == retry_button:
                # Restart the conversion
                if self.thread:
                    self.thread.stop()
                if self.progress_dialog:
                    self.progress_dialog.reject()
                self.convert_btn.setText("Convert")
                # Reset error_shown flag
                delattr(self, 'error_shown')
                # Small delay before restarting conversion
                QTimer.singleShot(500, self.toggle_conversion)
            # Skip button just continues processing

    def on_conversion_complete(self, last_output_path, input_size, output_size, success_count, failure_count):
        # First ensure the progress dialog is closed
        if self.progress_dialog and self.progress_dialog.isVisible():
            self.progress_dialog.accept()
        
        # Reset button text
        self.convert_btn.setText("Convert")
        
        # Calculate compression ratio
        compression_ratio = (output_size / input_size) * 100 if input_size > 0 else 0
        
        # Format sizes for display
        input_mb = input_size / (1024 * 1024)
        output_mb = output_size / (1024 * 1024)
        
        # Create completion message
        message = f"Conversion complete!\n\n"
        message += f"Files processed: {success_count} successful, {failure_count} failed\n"
        message += f"Input size: {input_mb:.2f} MB\n"
        message += f"Output size: {output_mb:.2f} MB\n"
        message += f"Compression ratio: {compression_ratio:.1f}%"
        
        # Create a custom message box with an "Open Output Folder" button
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Conversion Complete")
        msg_box.setText(message)
        msg_box.setIcon(QMessageBox.Icon.Information)
        
        # Add standard OK button
        ok_button = msg_box.addButton(QMessageBox.StandardButton.Ok)
        
        # Add custom button to open output folder
        open_folder_button = msg_box.addButton("Open Output Folder", QMessageBox.ButtonRole.ActionRole)
        
        # Increase button width by applying custom stylesheet
        msg_box.setStyleSheet("""
            QPushButton {
                min-width: 125px;  /* Increased from 120px to 125px */
                padding: 8px;
            }
        """)
        
        msg_box.exec()
        
        # Check which button was clicked
        if msg_box.clickedButton() == open_folder_button:
            # Open the output folder in file explorer
            try:
                if sys.platform == 'win32':
                    os.startfile(self.output_dir)
                elif sys.platform == 'darwin':  # macOS
                    subprocess.run(['open', self.output_dir])
                else:  # Linux
                    subprocess.run(['xdg-open', self.output_dir])
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not open output folder: {str(e)}")
        
        # Add to conversion history
        self.conversion_history.append({
            'timestamp': time.time(),
            'input_size': input_size,
            'output_size': output_size,
            'success_count': success_count,
            'failure_count': failure_count,
            'last_output': last_output_path
        })


class ProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Converting...")
        self.setFixedSize(450, 220)  # Increased height from 200 to 220
        self.setModal(True)
        
        # Set window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Apply parent's theme
        if hasattr(parent, 'is_dark_mode'):
            self.is_dark_mode = parent.is_dark_mode
            bg_color = COLORS['background_dark'] if self.is_dark_mode else COLORS['background_light']
            text_color = COLORS['text_dark'] if self.is_dark_mode else COLORS['text_light']
            self.setStyleSheet(f"background-color: {bg_color}; color: {text_color};")
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 25)  # Increased bottom margin from 20 to 25
        layout.setSpacing(15)
        
        # Title with modern font
        title = QLabel("Converting Files")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        
        # Progress bar with modern styling
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)
        
        # Status labels with modern font
        self.eta_label = QLabel("Preparing...")
        self.eta_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.eta_label)
        
        self.speed_label = QLabel("")
        self.speed_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.speed_label)
        
        layout.addStretch()
        
        # Cancel button with modern styling
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(45)  # Set fixed height directly
        # Add icon to Cancel button
        self.cancel_btn.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_DialogCancelButton))
        self.cancel_btn.setIconSize(QSize(16, 16))
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF3B30;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px;
                font-weight: 600;
                margin-bottom: 5px;  /* Added bottom margin to button */
            }
            QPushButton:hover {
                background-color: #FF6B6B;
            }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        layout.addWidget(self.cancel_btn)
        
        self.setLayout(layout)
    
    def update_progress(self, value, eta_text, speed_text):
        self.progress_bar.setValue(value)
        self.eta_label.setText(eta_text)
        self.speed_label.setText(speed_text)


def main():
    # Enable large image support in PIL
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None  # Disable DecompressionBomb warnings for large images
    
    app = QApplication(sys.argv)
    window = ImageConverter()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()