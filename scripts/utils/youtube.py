#!/usr/bin/env python3
"""
YouTube API wrapper with retry logic and live stream support
"""

import requests
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class YouTubeAPIError(Exception):
    """YouTube API related exception"""
    pass

class YouTubeAPI:
    def __init__(self, api_key: str, retry_attempts: int = 3, retry_delay: int = 2, timeout: int = 30):
        self.api_key = api_key
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.session = requests.Session()
    
    def _make_request(self, endpoint: str, params: Dict) -> Dict:
        """
        Make API request with retry logic and error handling.
        """
        url = f"{self.base_url}/{endpoint}"
        params['key'] = self.api_key
        
        for attempt in range(self.retry_attempts):
            try:
                logger.debug(f"API request attempt {attempt + 1}: {endpoint}")
                
                response = self.session.get(url, params=params, timeout=self.timeout)
                
                # Check HTTP status code
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check for API errors
                    if 'error' in data:
                        error_msg = data['error'].get('message', 'Unknown API error')
                        raise YouTubeAPIError(f"YouTube API error: {error_msg}")
                    
                    logger.debug(f"API request successful: {endpoint}")
                    return data
                
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
            params = {
                "part": "snippet,statistics,contentDetails",
                "id": channel_id
            }
            
            data = self._make_request("channels", params)
            
            if not data.get("items"):
                logger.warning(f"Channel not found: {channel_id}")
                return None
            
            return data["items"][0]
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get channel info ({channel_id}): {e}")
            return None
    
    def get_playlist_videos(self, playlist_id: str, max_results: int = 50) -> List[str]:
        """
        Get video IDs from a playlist.
        """
        try:
            video_ids = []
            next_page_token = None
            
            while len(video_ids) < max_results:
                params = {
                    "part": "snippet",
                    "playlistId": playlist_id,
                    "maxResults": min(50, max_results - len(video_ids))  # API limit: 50
                }
                
                if next_page_token:
                    params["pageToken"] = next_page_token
                
                data = self._make_request("playlistItems", params)
                
                for item in data.get("items", []):
                    video_id = item["snippet"]["resourceId"]["videoId"]
                    video_ids.append(video_id)
                
                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break
            
            logger.info(f"Collected {len(video_ids)} video IDs from playlist {playlist_id}")
            return video_ids
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get playlist videos ({playlist_id}): {e}")
            return []
    
    def get_videos_details(self, video_ids: List[str]) -> List[Dict]:
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
                
                params = {
                    "part": "snippet,statistics,contentDetails,liveStreamingDetails",
                    "id": ",".join(batch_ids)
                }
                
                data = self._make_request("videos", params)
                
                for item in data.get("items", []):
                    video_info = {
                        "videoId": item["id"],
                        "title": item["snippet"]["title"],
                        "description": item["snippet"]["description"][:500] + "..." if len(item["snippet"]["description"]) > 500 else item["snippet"]["description"],
                        "publishedAt": item["snippet"]["publishedAt"],
                        # "thumbnails": item["snippet"]["thumbnails"], # https://i.ytimg.com/vi/{vid}/{default,mqdefault,hqdefault,sddefault,maxresdefault}.jpg
                        "duration": item["contentDetails"]["duration"],
                        "viewCount": int(item["statistics"].get("viewCount", 0)),
                        "likeCount": int(item["statistics"].get("likeCount", 0)),
                        "commentCount": int(item["statistics"].get("commentCount", 0)),
                        "tags": item["snippet"].get("tags", [])[:10],  # Max 10 tags
                        "categoryId": item["snippet"].get("categoryId"),
                        "defaultLanguage": item["snippet"].get("defaultLanguage"),
                        "defaultAudioLanguage": item["snippet"].get("defaultAudioLanguage"),
                        "liveBroadcastContent": item["snippet"].get("liveBroadcastContent", "none"),
                        "liveStreamingDetails": None,
                    }
                    
                    # Add live streaming details if available
                    if "liveStreamingDetails" in item:
                        video_info["liveStreamingDetails"] = item["liveStreamingDetails"]
                    
                    all_videos.append(video_info)
                
                # Small delay to avoid rate limits
                if i + 50 < len(video_ids):
                    time.sleep(0.1)
            
            logger.info(f"Collected detailed info for {len(all_videos)} out of {len(video_ids)} videos")
            return all_videos
            
        except YouTubeAPIError as e:
            logger.error(f"Failed to get video details: {e}")
            return []
    
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
        video_ids = self.get_playlist_videos(uploads_playlist_id, max_results)
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
