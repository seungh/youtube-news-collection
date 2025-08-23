import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

class CacheManager:
    
    def __init__(self, cache_dir="cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
    def _get_cache_filename(self, request_key):
        """
        Hash request key and generate cache file name
        """
        hash_key = hashlib.md5(request_key.encode()).hexdigest()
        return self.cache_dir / f"etag_{hash_key}"
    
    def get_etag(self, request_key):
        """
        Get etag by request key
        """
        cache_file = self._get_cache_filename(request_key)
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('etag'), data.get('data')
            except (json.JSONDecodeError, KeyError, FileNotFoundError):
                return None, None
        return None, None
    
    def save_etag(self, request_key, etag, data):
        """
        save etag and data as a cache file
        """
        cache_file = self._get_cache_filename(request_key)
        cache_data = {
            'etag': etag,
            'data': data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)
    
    def clear_cache(self):
        """
        Clear all cache files
        """
        for cache_file in self.cache_dir.glob("etag_*"):
            cache_file.unlink()
