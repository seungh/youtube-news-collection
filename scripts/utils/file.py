#!/usr/bin/env python3
"""
File management utilities for YouTube data collection
Handles data merging and updating with live stream support
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class FileManager:
    def __init__(self, base_path: str = "assets/data"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def get_file_path(self, year: str, month: str, day: str) -> Path:
        """
        Get file path for year/month/day structure.
        """
        date_dir = self.base_path / year / month
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / f"{day}.json"
    
    def load_existing_data(self, file_path: Path) -> Dict:
        """
        Load existing JSON file. Returns empty structure if file doesn't exist.
        """
        null_data = {
                "videos": {},
                "channels": {},
                "lastUpdated": None,
                "totalVideos": 0,
                "totalChannels": 0
        }
        if not file_path.exists():
            return null_data
        
        try:            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # Validate and migrate existing data structure
                if not isinstance(data.get("videos"), dict):
                    data["videos"] = {}
                if not isinstance(data.get("channels"), dict):
                    data["channels"] = {}
                if "lastUpdated" not in data:
                    data["lastUpdated"] = None
                if "totalVideos" not in data:
                    data["totalVideos"] = len(data["videos"])
                if "totalChannels" not in data:
                    data["totalChannels"] = len(data["channels"])
                
            logger.info(f"Loaded existing data: {len(data['videos'])} videos, {len(data['channels'])} channels from {file_path}")
            return data
                
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Failed to load file ({file_path}): {e}, starting with empty structure")
            return null_data
    
    def save_data(self, data: Dict, file_path: Path) -> bool:
        """
        Save data to JSON file with atomic write operation.
        """
        try:
            # Write to temporary file first for atomic operation
            temp_path = file_path.with_suffix('.json.tmp')
            
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Move temporary file to actual file
            temp_path.replace(file_path)
            
            logger.info(f"Data saved successfully: {file_path}")
            logger.info(f"Total {data['totalVideos']} videos, {data['totalChannels']} channels")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save data ({file_path}): {e}")
            return False
    
    def update_data_with_videos(self, existing_data: Dict, channel_data: Dict) -> tuple[Dict, bool]:
        """
        Update existing data with new video information.
        Supports both regular videos and live streams.
        """
        def is_updated(dict1: Dict, dict2: Dict, excluded_keys: List) -> bool:
            """
            Check if two dictionaries are different.
            """
            filtered_dict1 = {k: v for k, v in dict1.items() if k not in excluded_keys}
            filtered_dict2 = {k: v for k, v in dict2.items() if k not in excluded_keys}
            return filtered_dict1 == filtered_dict2

        updated = False

        # Update channel information
        channel_id = channel_data["channelId"]
        channel_info = {
            "channelId": channel_id,
            "channelName": channel_data["channelName"],
            "channelUrl": channel_data["channelUrl"],
            "channelDescription": channel_data["channelDescription"],
            "channelThumbnails": channel_data["channelThumbnails"],
            "subscriberCount": channel_data["subscriberCount"],
            "viewCount": channel_data["viewCount"],
            "videoCount": channel_data["videoCount"],
            "lastUpdated": datetime.now(timezone.utc).isoformat().split("+")[0] + "Z"
        }
        
        # Check if channel info has changed
        if (channel_id not in existing_data["channels"] or 
            is_updated(existing_data["channels"][channel_id], channel_info, ["lastUpdated"])):
            existing_data["channels"][channel_id] = channel_info
            updated = True
            logger.info(f"Updated channel info: {channel_data['channelName']}")
        
        # Update video information
        new_videos_count = 0
        updated_videos_count = 0
        
        for video in channel_data["videos"]:
            video_id = video["videoId"]
            
            # Add channel info and collection timestamp to video data
            video_with_channel = {
                **video,
                "channelId": channel_id,
                "channelName": channel_data["channelName"],
                "collectedAt": datetime.now(timezone.utc).isoformat().split("+")[0] + "Z"
            }

            if video_id in existing_data["videos"]:
                if is_updated(existing_data["videos"][video_id], video_with_channel, ["collectedAt"]):
                    # Update existing video
                    existing_data["videos"][video_id] = video_with_channel
                    updated_videos_count += 1
                    updated = True
            else:
                # New video
                existing_data["videos"][video_id] = video_with_channel
                new_videos_count += 1
                updated = True
        
        if new_videos_count > 0:
            logger.info(f"Added {new_videos_count} new videos")
        
        if updated_videos_count > 0:
            logger.info(f"Updated {updated_videos_count} existing videos")
        
        # Update metadata
        if updated:
            existing_data["lastUpdated"] = datetime.now(timezone.utc).isoformat().split("+")[0] + "Z"
            existing_data["totalVideos"] = len(existing_data["videos"])
            existing_data["totalChannels"] = len(existing_data["channels"])
        
        return existing_data, updated
    
    def organize_videos_by_date(self, all_videos: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group videos by date based on their effective date.
        For regular videos: use publishedAt
        For live streams: use actual streaming time if available
        """
        from .date import get_effective_date_for_video, format_date_for_filename, is_live_stream
        
        grouped_videos = {}
        
        for video in all_videos:
            try:
                # Get effective date for this video (handles live streams)
                effective_date = get_effective_date_for_video(video)
                date_key = format_date_for_filename(effective_date)
                
                if date_key not in grouped_videos:
                    grouped_videos[date_key] = []
                
                grouped_videos[date_key].append(video)
                
                # Log live stream handling
                if is_live_stream(video):
                    logger.debug(f"Live stream video {video.get('videoId', 'unknown')} assigned to date: {date_key}")
                
            except Exception as e:
                logger.warning(f"Failed to parse date for video ({video.get('videoId', 'unknown')}): {e}")
                continue
        
        logger.info(f"Organized videos into {len(grouped_videos)} date groups")
        return grouped_videos
    
    def get_existing_files(self) -> List[Path]:
        """
        Get list of all existing data files.
        """
        files = []
        for year_dir in self.base_path.iterdir():
            if year_dir.is_dir() and year_dir.name.isdigit():
                for month_dir in year_dir.iterdir():
                    if month_dir.is_dir() and month_dir.name.isdigit():
                        for day_file in month_dir.glob("*.json"):
                            files.append(day_file)
        
        return sorted(files)
    
    def create_summary_file(self) -> Dict:
        """
        Create summary information for all data files.
        Includes live stream statistics.
        """
        summary = {
            "generatedAt": datetime.now(timezone.utc).isoformat().split("+")[0] + "Z",
            "totalFiles": 0,
            "totalVideos": 0,
            "totalLiveStreams": 0,
            "totalUpcoming": 0,
            "totalChannels": set(),
            "dateRange": {
                "start": None,
                "end": None
            },
            "files": []
        }
        
        files = self.get_existing_files()
        
        for file_path in files:
            try:
                data = self.load_existing_data(file_path)
                
                # Count live streams
                live_stream_count = 0
                upcoming_count = 0
                for video_id, video_data in data["videos"].items():
                    if video_data.get("liveBroadcastContent") == "live":
                        live_stream_count += 1
                    if video_data.get("liveBroadcastContent") == "upcoming":
                        upcoming_count += 1
                
                file_info = {
                    "path": str(file_path.relative_to(self.base_path)),
                    "videoCount": data["totalVideos"],
                    "liveStreamCount": live_stream_count,
                    "upcomingCount": upcoming_count,
                    "channelCount": data["totalChannels"],
                    "lastUpdated": data["lastUpdated"]
                }
                
                summary["files"].append(file_info)
                summary["totalVideos"] += data["totalVideos"]
                summary["totalLiveStreams"] += live_stream_count
                summary["totalUpcoming"] += upcoming_count
                summary["totalChannels"].update(data["channels"].keys())
                
                # Update date range
                date_str = file_path.stem  # Extract date from filename
                if not summary["dateRange"]["start"] or date_str < summary["dateRange"]["start"]:
                    summary["dateRange"]["start"] = date_str
                if not summary["dateRange"]["end"] or date_str > summary["dateRange"]["end"]:
                    summary["dateRange"]["end"] = date_str
                
            except Exception as e:
                logger.warning(f"Failed to process file during summary creation ({file_path}): {e}")
        
        summary["totalFiles"] = len(files)
        summary["totalChannels"] = len(summary["totalChannels"])
        
        # Save summary file
        summary_path = self.base_path / "summary.json"
        try:
            with open(summary_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"Summary file created: {summary_path}")
        except Exception as e:
            logger.error(f"Failed to create summary file: {e}")
        
        return summary
