#!/usr/bin/env python3
"""
YouTube API wrapper with retry logic and live stream support
"""

import requests
import time
import logging
from typing import Dict, List, Optional
from .cache import CacheManager

logger = logging.getLogger(__name__)

class YouTubeAPIError(Exception):
    """YouTube API related exception"""
    pass

class QuotaTracker:
    """
    Quota Tracker Class
    """
    QUOTA_COSTS = {
        'channels': 1,
        'playlistItems': 1,
        'videos': 1,
        'playlists': 1,
        'search': 100,
    }
    
    def __init__(self):
        self.quota_used = 0
        self.requests_made = {}
        self.cache_hits = 0
        self.start_time = time.time()
    
    def add_request(self, endpoint, cached=False):
        if cached:
            self.cache_hits += 1
            return
        
        cost = self.QUOTA_COSTS.get(endpoint, 1)
        self.quota_used += cost
        
        if endpoint not in self.requests_made:
            self.requests_made[endpoint] = 0
        self.requests_made[endpoint] += 1
    
    def get_summary(self):
        duration = time.time() - self.start_time
        total_requests = sum(self.requests_made.values())
        return {
            'quota_used': self.quota_used,
            'total_requests': total_requests,
            'cache_hits': self.cache_hits,
            'cache_hit_rate': f"{(self.cache_hits / (total_requests + self.cache_hits) * 100):.1f}%" if (total_requests + self.cache_hits) > 0 else "0.0%",
            'requests_by_endpoint': self.requests_made,
        }

class YouTubeAPI:
    def __init__(self, api_key: str, retry_attempts: int = 3, retry_delay: int = 2, timeout: int = 30, use_cache=True, cache_dir="cache"):
        self.api_key = api_key
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.use_cache = use_cache
        self.cache_manager = CacheManager(cache_dir) if use_cache else None
        self.quota_tracker = QuotaTracker()
    
    def _create_request_key(self, endpoint, params: Dict) -> str:
        """
        Create a key for API request.
        """
        cache_params = {k: v for k, v in params.items() if k != 'key'}
        sorted_params = sorted(cache_params.items())
        param_str = "&".join([f"{k}={v}" for k, v in sorted_params])
        return f"{endpoint}?{param_str}"

    def _make_request(self, endpoint: str, params: Dict) -> Dict:
        """
        Make API request with retry logic and error handling.
        """
        cached_data = None
        headers = {}
        
        if self.use_cache:
            request_key = self._create_request_key(endpoint, params)
            stored_etag, cached_data = self.cache_manager.get_etag(request_key)
            if stored_etag:
                logger.info(f"Using etag for request: {request_key}")
                headers['If-None-Match'] = stored_etag

        url = f"{self.base_url}/{endpoint}"
        params['key'] = self.api_key
        
        for attempt in range(self.retry_attempts):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                
                # Check HTTP status code
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check for API errors
                    if 'error' in data:
                        error_msg = data['error'].get('message', 'Unknown API error')
                        raise YouTubeAPIError(f"YouTube API error: {error_msg}")
                    
                    # Store etag and response in cache
                    if self.use_cache:
                        etag = data.get('etag')
                        if etag:
                            self.cache_manager.save_etag(request_key, etag, data)
                            logger.info(f"Saved ETag({etag}) for request: {request_key}")
                        self.quota_tracker.add_request(endpoint, False)

                    logger.debug(f"API request successful: {endpoint}")
                    return data
                
                elif response.status_code == 304:
                    # Not modified - return cached data
                    logger.info(f"Data not modified (304). Using cached data for: {request_key}")
                    if self.use_cache and cached_data:
                        self.quota_tracker.add_request(endpoint, True)
                        return cached_data
                    else:
                        raise YouTubeAPIError("Received 304 but no cached data available")

                elif response.status_code == 403:
                    # Quota exceeded or permission error
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', 'Forbidden')
                    
                    if 'quotaExceeded' in error_msg:
                        raise YouTubeAPIError(f"API quota exceeded: {error_msg}")
                    else:
                        raise YouTubeAPIError(f"API permission error: {error_msg}")
                
                elif response.status_code == 404:
                    # Resource not found
                    raise YouTubeAPIError(f"Resource not found: {endpoint}")
                
                else:
                    # Other HTTP errors
                    logger.warning(f"HTTP {response.status_code} error, retrying... ({attempt + 1}/{self.retry_attempts})")
                    response.raise_for_status()
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout, retrying... ({attempt + 1}/{self.retry_attempts})")
                
            except requests.exceptions.ConnectionError:
                logger.warning(f"Connection error, retrying... ({attempt + 1}/{self.retry_attempts})")
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request error: {e}, retrying... ({attempt + 1}/{self.retry_attempts})")
            
            except YouTubeAPIError:
                # Don't retry API-related errors
                raise
            
            # Wait before next attempt (exponential backoff)
            if attempt < self.retry_attempts - 1:
                time.sleep(self.retry_delay * (2 ** attempt))
        
        # All retry attempts failed
        raise YouTubeAPIError(f"API request failed: {endpoint} (all retries failed)")
    
    def get_channel_info(self, channel_id: str) -> Optional[Dict]:
        """
        Get basic channel information.
        """
        try:
            data = self._make_request("channels", {
                "part": "snippet, statistics, contentDetails",
                "id": channel_id
            })            
            if not data.get("items"):
                logger.warning(f"Channel not found: {channel_id}")
                return None
            
            return data["items"][0]
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get channel info ({channel_id}): {e}")
            raise
            # return None

    def get_playlist_videos(self, playlist_id: str, channel_id: str, max_results: int = 50) -> List[str]:
        """
        Get video IDs from a playlist.
        """
        try:
            video_ids = []
            next_page_token = None
            logger.info(f"Searching video IDs..: {playlist_id}")
            
            while len(video_ids) < max_results:
                response = self._make_request("playlistItems", {
                    "part": "snippet",
                    "playlistId": playlist_id,
                    "maxResults": min(50, max_results - len(video_ids)),
                    "pageToken": next_page_token
                })                
                for item in response.get("items", []):
                    if item["snippet"]["channelId"] != channel_id:
                        continue
                    video_ids.append(item["snippet"]["resourceId"]["videoId"])
                
                next_page_token = response.get("nextPageToken")
                if not next_page_token:
                    break
            
            logger.info(f"Collected {len(video_ids)} video IDs from playlist {playlist_id}")
            return video_ids
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get playlist videos ({playlist_id}): {e}")
            raise
            # return []
    
    def get_videos_details(self, video_ids: List[str], channel_id: str=None) -> List[Dict]:
        """
        Get detailed information for a list of video IDs.
        Processes in batches of 50 due to API limitations.
        Includes live streaming details for live content.
        """
        try:
            all_videos = []
            
            # Process in batches of 50
            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i:i + 50]
                response = self._make_request("videos", params = {
                    "part": "snippet, statistics, contentDetails, liveStreamingDetails",
                    "id": ",".join(batch_ids)
                })
                for item in response.get("items", []):
                    try:
                        video_info = {
                            "videoId": item["id"],
                            "title": item["snippet"]["title"],
                            "description": item["snippet"]["description"],
                            "publishedAt": item["snippet"]["publishedAt"],
                            "duration": item["contentDetails"]["duration"],
                            "viewCount": int(item["statistics"].get("viewCount", 0)),
                            "likeCount": int(item["statistics"].get("likeCount", 0)),
                            "commentCount": int(item["statistics"].get("commentCount", 0)),
                            "tags": item["snippet"].get("tags", []),
                            "liveBroadcastContent": item["snippet"].get("liveBroadcastContent", "none"),
                            "liveStreamingDetails": None,
                            "actualPubAt": None,
                        }
                    except:
                        continue
                    
                    # Add live streaming details if available
                    if "liveStreamingDetails" in item:
                        video_info["liveStreamingDetails"] = item["liveStreamingDetails"]

                    # Add actual publish time 
                    if video_info["liveStreamingDetails"]:
                        if "actualStartTime" in video_info["liveStreamingDetails"].keys():
                            video_info["actualPubAt"] = video_info["liveStreamingDetails"]["actualStartTime"]
                        elif "scheduledStartTime" in video_info["liveStreamingDetails"].keys():
                            video_info["actualPubAt"] = video_info["liveStreamingDetails"]["scheduledStartTime"]
                    else:
                        video_info["actualPubAt"] = video_info["publishedAt"]
                    
                    all_videos.append(video_info)
                
                # Small delay to avoid rate limits
                if i + 50 < len(video_ids):
                    time.sleep(0.1)
            
            logger.info(f"Collected detailed info for {len(all_videos)} out of {len(video_ids)} videos")
            return all_videos
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get video details: {e}")
            raise
            # return []
    
    def get_channel_videos(self, channel_id: str, max_results: int = 50) -> Optional[Dict]:
        """
        Get all video information from a channel.
        Includes both regular videos and live streams.
        """
        # 1. Get channel information
        channel_info = self.get_channel_info(channel_id)
        if not channel_info:
            return None
        
        # 2. Get uploads playlist ID
        uploads_playlist_id = channel_info["contentDetails"]["relatedPlaylists"]["uploads"]
        
        # 3. Get video IDs list
        video_ids = self.get_playlist_videos(uploads_playlist_id, channel_id, max_results)
        if not video_ids:
            logger.warning(f"No videos found for channel {channel_id}")
        
        # 4. Get detailed video information
        videos = self.get_videos_details(video_ids) if video_ids else []
        
        return {
            "channelId": channel_id,
            "channelName": channel_info["snippet"]["title"],
            "channelUrl": channel_info["snippet"]["customUrl"],
            "channelDescription": channel_info["snippet"]["description"],
            "channelThumbnails": channel_info["snippet"].get("thumbnails", {}),
            "subscriberCount": int(channel_info["statistics"].get("subscriberCount", 0)),
            "viewCount": int(channel_info["statistics"].get("viewCount", 0)),
            "videoCount": int(channel_info["statistics"].get("videoCount", 0)),
            "videos": videos
        }

    def search_playlists(self, channel_id):
        """
        Get all playlist IDs from a channel.
        """
        next_page_token = None
        playlists = []

        while True:
            response = self._make_request("playlists", params = {
                "part": "snippet",
                "channelId": channel_id,
                "maxResults": 50,
                "pageToken": next_page_token,
            })
            if response and response.get("items"):
                for item in response["items"]:
                    playlists.append(item["id"])

            next_page_token = response.get("nextPageToken") if response else None
            if not next_page_token:
                break

        return playlists

    def search_all_videos(self, channel_id, saved_works):
        """
        search all videos from a channel
        """
        channel_info = self.get_channel_info(channel_id)
        saved_works["channel_info"] = channel_info

        pls = self.search_playlists(channel_id)
        up_pl = channel_info["contentDetails"]["relatedPlaylists"]["uploads"]
        if up_pl not in pls:
            pls.append(up_pl)

        for pl in pls:
            if pl in saved_works["processed_pl"]:
                continue
            
            vids = self.get_playlist_videos(pl, channel_id, 50000)
            logger.info(f"Playlist processed: {pl}")
            saved_works["processed_pl"].append(pl)
            for vid in vids:
                if vid in saved_works["collected_vids"]:
                    continue
                saved_works["collected_vids"].append(vid)
            logger.info(f"Unique videos found: {len(saved_works['collected_vids'])}")

        while saved_works["collected_vids"]:
            chunk = saved_works["collected_vids"][:50]
            videos = self.get_videos_details(chunk, channel_id) if chunk else []
            saved_works["videos"] += videos
            del saved_works["collected_vids"][:50]
        
        return {
            "channelId": channel_id,
            "channelName": channel_info["snippet"]["title"],
            "channelUrl": channel_info["snippet"]["customUrl"],
            "channelDescription": channel_info["snippet"]["description"],
            "channelThumbnails": channel_info["snippet"].get("thumbnails", {}),
            "subscriberCount": int(channel_info["statistics"].get("subscriberCount", 0)),
            "viewCount": int(channel_info["statistics"].get("viewCount", 0)),
            "videoCount": int(channel_info["statistics"].get("videoCount", 0)),
            "videos": saved_works["videos"]
        }