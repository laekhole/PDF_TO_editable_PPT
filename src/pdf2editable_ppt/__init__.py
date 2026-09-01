"""pdf2editable-ppt: non-destructive PDF -> editable PowerPoint conversion."""

from .converter import ConversionResult, convert
from .pipeline import ConvertOptions

__all__ = ["convert", "ConversionResult", "ConvertOptions"]
__version__ = "0.1.0"
