"""CLI argument extraction utilities."""

from typing import Any, Optional, Union, List
import argparse


class ArgsExtractor:
    """Utility for extracting common CLI argument patterns."""
    
    def __init__(self, args: argparse.Namespace):
        self.args = args
    
    def extract_template_id(self) -> Optional[str]:
        """Extract template_id from positional args or flags."""
        # Check flag first
        if hasattr(self.args, 'template_id') and self.args.template_id:
            return self.args.template_id
        
        # Check positional args
        if hasattr(self.args, 'template_ids') and self.args.template_ids:
            return self.args.template_ids[0]
        
        return None
    
    def extract_request_ids(self) -> List[str]:
        """Extract request IDs from positional args or flags."""
        request_ids = []
        
        # From flags
        if hasattr(self.args, 'request_id') and self.args.request_id:
            if isinstance(self.args.request_id, list):
                request_ids.extend(self.args.request_id)
            else:
                request_ids.append(self.args.request_id)
        
        # From positional args
        if hasattr(self.args, 'request_ids') and self.args.request_ids:
            request_ids.extend(self.args.request_ids)
        
        return list(set(request_ids))  # Remove duplicates
    
    def extract_machine_id(self) -> Optional[str]:
        """Extract machine_id from positional args or flags."""
        # Check flag first
        if hasattr(self.args, 'machine_id') and self.args.machine_id:
            return self.args.machine_id
        
        # Check positional args
        if hasattr(self.args, 'machine_ids') and self.args.machine_ids:
            return self.args.machine_ids[0]
        
        return None
    
    def extract_provider_api(self) -> Optional[str]:
        """Extract provider_api from args."""
        return getattr(self.args, 'provider_api', None)
    
    def extract_count(self, default: int = 1) -> int:
        """Extract count from args with default."""
        if hasattr(self.args, 'count') and self.args.count is not None:
            return self.args.count
        
        # Check positional args for count (second position)
        if hasattr(self.args, 'template_ids') and len(self.args.template_ids) > 1:
            try:
                return int(self.args.template_ids[1])
            except (ValueError, IndexError):
                pass
        
        return default
    
    def extract_metadata(self) -> dict[str, Any]:
        """Extract metadata from args."""
        metadata = {}
        
        # Common metadata fields
        if hasattr(self.args, 'dry_run') and self.args.dry_run:
            metadata['dry_run'] = True
        
        if hasattr(self.args, 'metadata') and self.args.metadata:
            # Handle key=value pairs
            for item in self.args.metadata:
                if '=' in item:
                    key, value = item.split('=', 1)
                    # Try to parse as int/bool
                    if value.lower() in ('true', 'false'):
                        metadata[key] = value.lower() == 'true'
                    elif value.isdigit():
                        metadata[key] = int(value)
                    else:
                        metadata[key] = value
        
        return metadata
    
    def extract_file_path(self) -> Optional[str]:
        """Extract file path from args."""
        return getattr(self.args, 'file', None)
    
    def extract_output_format(self, default: str = 'table') -> str:
        """Extract output format from args."""
        return getattr(self.args, 'output', default)
    
    def extract_provider_override(self) -> Optional[str]:
        """Extract provider override from global args."""
        return getattr(self.args, 'provider', None)
    
    def has_flag(self, flag_name: str) -> bool:
        """Check if a boolean flag is set."""
        return getattr(self.args, flag_name, False)
    
    def get_value(self, key: str, default: Any = None) -> Any:
        """Get any value from args with default."""
        return getattr(self.args, key, default)