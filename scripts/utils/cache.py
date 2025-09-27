import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

class CacheManager:
    
    def __init__(self, cache_dir="cache", max_cache_age_days=7, max_cache_files=1000):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.max_cache_age_days = max_cache_age_days
        self.max_cache_files = max_cache_files
        
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
                # remove corrupted cache file
                try:
                    cache_file.unlink()
                except:
                    pass
                return None, None
        return None, None
    
    def save_etag(self, request_key, etag, data):
        """
        save etag and data as a cache file and remove old cache file if exists
        """
        cache_file = self._get_cache_filename(request_key)
        cache_data = {
            'etag': etag,
            'data': data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # remove old cache file if exists
        if cache_file.exists():
            try:
                cache_file.unlink()
            except:
                pass
        
        # save new cache file
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False)
    
    def cleanup_old_cache(self):
        """
        clean up cache files older than max_cache_age_days
        """
        if not self.cache_dir.exists():
            return
            
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=self.max_cache_age_days)
        deleted_count = 0
        
        for cache_file in self.cache_dir.glob("etag_*"):
            try:
                # check file modification time
                file_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc)
                
                if file_mtime < cutoff_time:
                    cache_file.unlink()
                    deleted_count += 1
                    
            except Exception:
                # ignore errors and continue
                continue
        
        # if deleted_count > 0:
        #     print(f"Cleaned up {deleted_count} old cache files")
    
    def cleanup_excess_cache(self):
        """
        clean up excess cache files if total exceeds max_cache_files
        """
        if not self.cache_dir.exists():
            return
            
        cache_files = list(self.cache_dir.glob("etag_*"))
        
        if len(cache_files) <= self.max_cache_files:
            return
        
        # sort files by modification time (oldest first)
        cache_files.sort(key=lambda f: f.stat().st_mtime)
        
        # delete oldest files exceeding max_cache_files
        excess_count = len(cache_files) - self.max_cache_files
        deleted_count = 0
        
        for cache_file in cache_files[:excess_count]:
            try:
                cache_file.unlink()
                deleted_count += 1
            except Exception:
                continue
        
        # if deleted_count > 0:
        #     print(f"Cleaned up {deleted_count} excess cache files")
    
    def get_cache_stats(self):
        """
        Get cache statistics
        """
        if not self.cache_dir.exists():
            return {
                'total_files': 0,
                'total_size_mb': 0,
                'oldest_file': None,
                'newest_file': None
            }
        
        cache_files = list(self.cache_dir.glob("etag_*"))
        total_size = sum(f.stat().st_size for f in cache_files if f.exists())
        
        if cache_files:
            file_times = [f.stat().st_mtime for f in cache_files if f.exists()]
            oldest_time = min(file_times) if file_times else None
            newest_time = max(file_times) if file_times else None
        else:
            oldest_time = newest_time = None
        
        return {
            'total_files': len(cache_files),
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'oldest_file': datetime.fromtimestamp(oldest_time).isoformat() if oldest_time else None,
            'newest_file': datetime.fromtimestamp(newest_time).isoformat() if newest_time else None
        }
    
    def maintain_cache(self):
        """
        maintain cache by cleaning up old and excess files
        """
        self.cleanup_old_cache()
        self.cleanup_excess_cache()
    
    def clear_cache(self):
        """
        Clear all cache files
        """
        deleted_count = 0
        for cache_file in self.cache_dir.glob("etag_*"):
            try:
                cache_file.unlink()
                deleted_count += 1
            except:
                continue
        
        # if deleted_count > 0:
        #     print(f"Cleared {deleted_count} cache files")