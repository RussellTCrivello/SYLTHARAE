"""
Video file reader - Extract metadata from video files
Aligned with database design principles - all functions within class
Supports: MP4, AVI, MKV, MOV, WMV, FLV, WEBM
"""

import os
import logging
from typing import Dict, Any, Optional, Set
from pathlib import Path

from .base_reader import BaseReader

from Hdg_Err_Ex_Log import (
    handle_error,
    ErrorCategory,
    ErrorSeverity,
    format_validation_error
)

logger = logging.getLogger(__name__)


class VideoFileReader(BaseReader):
    """
    Reader for video files.
    
    Follows database design principles:
    - All functions are within the class
    - Inherits from BaseReader
    - Consistent error handling
    - Proper resource management
    """
    
    def get_supported_extensions(self) -> Set[str]:
        """Return set of supported video extensions"""
        return {
            '.mp4', '.avi', '.mkv', '.mov', '.wmv', 
            '.flv', '.webm', '.m4v', '.mpg', '.mpeg',
            '.3gp', '.ogv', '.ts', '.mts', '.m2ts',
            '.vob', '.rm', '.rmvb', '.asf', '.divx'
        }
    
    def read_file(self, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Read video file and extract metadata with improved error handling
        
        Args:
            file_info: Dictionary containing file information with 'path' key
        
        Returns:
            Dictionary with video metadata or error information
        """
        # Validate file info using base class method
        is_valid, error_msg = self.validate_file_info(file_info)
        if not is_valid:
            return self.create_error_result(error_msg or "Invalid file info", file_info.get("path", "unknown"))
        
        file_path = str(file_info.get("path"))
        
        try:
            return self.read_video_file(file_path)
        except Exception as e:
            return self.handle_read_error(e, file_path, "read_file")
    
    def read_video_file(self, filepath: str) -> Dict[str, Any]:
        """
        Extract metadata from video file with improved error handling
        
        Args:
            filepath: Path to video file
        
        Returns:
            Dictionary with video metadata including duration, resolution, codec, bitrate
        """
        result: Dict[str, Any] = {
            "filepath": filepath,
            "format": Path(filepath).suffix.lower()
        }
        
        # Validate filepath
        if not filepath or not isinstance(filepath, str):
            error_msg = format_validation_error('filepath', filepath, 'must be a non-empty string')
            handle_error(
                ValueError(error_msg),
                category=ErrorCategory.VALIDATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file'}
            )
            result["error"] = error_msg
            return result
        
        if not os.path.exists(filepath):
            error_msg = "File not found"
            handle_error(
                FileNotFoundError(f"{error_msg}: {filepath}"),
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        
        try:
            file_size = os.path.getsize(filepath)
            result["file_size"] = file_size
            result["file_size_formatted"] = self._format_file_size(file_size)
        except OSError as e:
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = f"Cannot get file size: {str(e)}"
            return result
        
        # Try opencv first (most reliable and fastest)
        try:
            import cv2
            
            cap = cv2.VideoCapture(filepath)
            
            if not cap.isOpened():
                raise Exception("Could not open video file")
            
            result["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            result["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            result["resolution"] = f"{result['width']}x{result['height']}"
            result["fps"] = cap.get(cv2.CAP_PROP_FPS)
            result["frame_count"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            if result["fps"] > 0:
                result["duration_seconds"] = result["frame_count"] / result["fps"]
                result["duration_formatted"] = self._format_duration(result["duration_seconds"])
            
            # Determine video quality based on resolution
            if result["height"] >= 2160:
                result["quality"] = "4K (2160p)"
            elif result["height"] >= 1440:
                result["quality"] = "2K (1440p)"
            elif result["height"] >= 1080:
                result["quality"] = "Full HD (1080p)"
            elif result["height"] >= 720:
                result["quality"] = "HD (720p)"
            elif result["height"] >= 480:
                result["quality"] = "SD (480p)"
            else:
                result["quality"] = "Low Resolution"
            
            # Try to get codec information
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
            if fourcc > 0:
                codec_bytes = fourcc.to_bytes(4, byteorder='little')
                try:
                    result["codec"] = codec_bytes.decode('ascii').strip('\x00')
                except:
                    result["codec"] = "Unknown"
            
            cap.release()
            return result
            
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"opencv failed: {e}")
        
        # Try moviepy as fallback
        try:
            from moviepy.editor import VideoFileClip
            
            with VideoFileClip(filepath) as clip:
                result["duration_seconds"] = clip.duration
                result["duration_formatted"] = self._format_duration(clip.duration)
                result["fps"] = clip.fps
                result["width"] = clip.w
                result["height"] = clip.h
                result["resolution"] = f"{clip.w}x{clip.h}"
                result["has_audio"] = clip.audio is not None
                
                # Determine quality
                if result["height"] >= 2160:
                    result["quality"] = "4K (2160p)"
                elif result["height"] >= 1440:
                    result["quality"] = "2K (1440p)"
                elif result["height"] >= 1080:
                    result["quality"] = "Full HD (1080p)"
                elif result["height"] >= 720:
                    result["quality"] = "HD (720p)"
                elif result["height"] >= 480:
                    result["quality"] = "SD (480p)"
                else:
                    result["quality"] = "Low Resolution"
                
                if clip.audio:
                    result["audio_fps"] = clip.audio.fps
                    result["audio_channels"] = clip.audio.nchannels
            
            return result
            
        except ImportError as e:
            error_msg = "No video library installed. Install with: pip install opencv-python or pip install moviepy"
            handle_error(
                e,
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
        except Exception as e:
            error_msg = f"Error reading video file: {str(e)}"
            handle_error(
                e,
                category=ErrorCategory.FILE_PROCESSING,
                severity=ErrorSeverity.MEDIUM,
                context={'operation': 'read_video_file', 'filepath': filepath}
            )
            result["error"] = error_msg
            return result
    
    def _format_duration(self, seconds):
        """Format duration in HH:MM:SS"""
        if not seconds or seconds <= 0:
            return "00:00"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
    
    def _format_file_size(self, size_bytes):
        """Format file size in human-readable format"""
        if size_bytes is None:
            return "N/A"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"



