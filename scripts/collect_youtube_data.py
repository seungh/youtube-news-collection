#!/usr/bin/env python3
"""
YouTube Data Collector - Main Script
Collects video data based on publish date for regular videos and streaming time for live broadcasts
"""

import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add utils module path based on current script directory
current_dir = Path(__file__).parent
sys.path.append(str(current_dir))

from utils.youtube import YouTubeAPI, YouTubeAPIError
from utils.file import FileManager
from utils.date import get_date_path, format_date_for_filename, convert_time, parse_youtube_date
from utils.log import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


class YouTubeDataCollector:
    def __init__(self, api_key: str, config_path: str = "config.json"):
        """
        Initialize YouTube data collector with live stream support
        """
        self.config_path = Path(current_dir) / config_path
        self.config = self._load_config()
        
        # Initialize API client
        self.settings = self.config.get("settings", {})
        self.api = YouTubeAPI(
            api_key=api_key,
            retry_attempts=self.settings.get("retryAttempts", 3),
            retry_delay=self.settings.get("retryDelay", 2),
            timeout=self.settings.get("requestTimeout", 30)
        )
        
        # Initialize file manager
        self.file_manager = FileManager()
        
        # Thread safety lock
        self.lock = threading.Lock()
        
        # Statistics tracking
        self.stats = {
            "total_videos_processed": 0,
            "total_videos_new": 0,
            "total_videos_updated": 0,
            "total_live_streams": 0,
            "total_upcoming": 0,
            "total_channels_processed": 0,
            "total_files_updated": 0,
            "errors": []
        }
    
    def _load_config(self) -> dict:
        """
        Load configuration file.
        """
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info(f"Configuration loaded successfully: {self.config_path}")
                return config
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in configuration file: {e}")
            raise
    
    def get_enabled_channels(self) -> list:
        """
        Get list of enabled channels from configuration.
        """
        enabled_channels = []
        for channel in self.config.get("channels", []):
            if channel.get("enabled", True):
                enabled_channels.append(channel)
        
        logger.info(f"Found {len(enabled_channels)} enabled channels")
        return enabled_channels
    
    def collect_channel_data(self, channel_config: dict) -> dict:
        """
        Collect data from a single channel.
        """
        channel_name = channel_config.get("name", "Unknown")
        channel_id = channel_config["channelId"]
        max_videos = channel_config.get("maxVideos", 50)
        
        logger.info(f"Starting data collection for channel: {channel_name} (max {max_videos} videos)")
        
        try:
            channel_data = self.api.get_channel_videos(channel_id, max_videos)
            
            if channel_data and channel_data["videos"]:
                # Count live streams
                live_stream_count = sum(1 for video in channel_data["videos"] if video["liveBroadcastContent"] == "live")
                upcoming_count = sum(1 for video in channel_data["videos"] if video["liveBroadcastContent"] == "upcoming")
                
                logger.info(f"{channel_name}: {len(channel_data['videos'])} videos collected ({live_stream_count} live streams, {upcoming_count} upcoming)")
                
                with self.lock:
                    self.stats["total_channels_processed"] += 1
                    self.stats["total_live_streams"] += live_stream_count
                    self.stats["total_upcoming"] += upcoming_count
                
                return channel_data
            else:
                logger.warning(f"❌ {channel_name}: No video data available")
                with self.lock:
                    self.stats["errors"].append(f"{channel_name}: No video data available")
                return None
                
        except YouTubeAPIError as e:
            logger.error(f"❌ {channel_name} API error: {e}")
            with self.lock:
                self.stats["errors"].append(f"{channel_name}: {str(e)}")
            return None

        except Exception as e:
            logger.error(f"❌ {channel_name} unexpected error: {e}")
            with self.lock:
                self.stats["errors"].append(f"{channel_name}: {str(e)}")
            return None
    
    def process_videos_by_date(self, all_channel_data: list) -> dict:
        """
        Group all videos by their effective date.
        Regular videos: use publishedAt
        Live streams: use actual streaming time when available
        """
        videos_by_date = {}
        
        for channel_data in all_channel_data:
            if not channel_data or not channel_data["videos"]:
                continue
            
            channel_id = channel_data["channelId"]

            for video in channel_data["videos"]:
                try:
                    pub_at = video['actualPubAt'] if video['actualPubAt'] else video['publishedAt']
                    pub_at = convert_time(parse_youtube_date(pub_at), self.settings.get("timezone", "UTC"))
                    date_key = format_date_for_filename(pub_at)
                    
                    if date_key not in videos_by_date:
                        videos_by_date[date_key] = {}
                    
                    if channel_id not in videos_by_date[date_key]:
                        videos_by_date[date_key][channel_id] = {
                            "channelId": channel_id,
                            "channelName": channel_data["channelName"],
                            "channelUrl": channel_data["channelUrl"],
                            "channelDescription": channel_data["channelDescription"],
                            "channelThumbnails": channel_data["channelThumbnails"],
                            "subscriberCount": channel_data["subscriberCount"],
                            "videoCount": channel_data["videoCount"],
                            "viewCount": channel_data["viewCount"],
                            "videos": []
                        }
                    
                    videos_by_date[date_key][channel_id]["videos"].append(video)
                    
                    with self.lock:
                        self.stats["total_videos_processed"] += 1

                except Exception as e:
                    logger.warning(f"Failed to process video date ({video.get('videoId', 'unknown')}): {e}")
                    continue
        
        logger.info(f"Video grouping completed: {len(videos_by_date)} date groups")
        return videos_by_date
    
    def update_data_files(self, videos_by_date: dict) -> None:
        """
        Update data files with grouped video data by date.
        """
        for date_str, channels_data in videos_by_date.items():
            try:
                # Parse date string
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                date_path = get_date_path(date_obj)
                
                # Create file path
                file_path = self.file_manager.get_file_path(
                    date_path["year"],
                    date_path["month"],
                    date_path["day"]
                )
                
                # Load existing data
                existing_data = self.file_manager.load_existing_data(file_path)
                
                # Update with each channel's data
                for channel_data in channels_data.values():
                    updated_data, is_updated = self.file_manager.update_data_with_videos(
                        existing_data, channel_data
                    )
                    existing_data = updated_data
                
                # Save file if there were changes
                if is_updated:
                    success = self.file_manager.save_data(existing_data, file_path)
                    if success:
                        with self.lock:
                            self.stats["total_files_updated"] += 1
                        logger.info(f"File updated for date: {date_str}")
                    else:
                        with self.lock:
                            self.stats["errors"].append(f"Failed to save file for date: {date_str}")
                else:
                    logger.info(f"No changes for date: {date_str}")
                    
            except Exception as e:
                logger.error(f"Error processing date {date_str}: {e}", exc_info=True)
                with self.lock:
                    self.stats["errors"].append(f"Date {date_str} processing error: {str(e)}")
    
    def collect_all_data(self) -> None:
        """
        Collect data from all channels and update files.
        """
        logger.info("=== Starting YouTube data collection ===")
        start_time = datetime.now()
        
        # Get enabled channels
        enabled_channels = self.get_enabled_channels()
        
        if not enabled_channels:
            logger.warning("No channels to collect data from")
            return
        
        # Collect channel data in parallel
        all_channel_data = []
        max_workers = self.settings.get("maxWorkers", 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_channel = {
                executor.submit(self.collect_channel_data, channel): channel
                for channel in enabled_channels
            }
            
            for future in as_completed(future_to_channel):
                channel = future_to_channel[future]
                try:
                    result = future.result()
                    if result:
                        all_channel_data.append(result)
                except Exception as e:
                    logger.error(f"Exception processing channel {channel.get('name', 'Unknown')}: {e}")
                    with self.lock:
                        self.stats["errors"].append(f"Channel {channel.get('name', 'Unknown')}: {str(e)}")
        
        if not all_channel_data:
            logger.error("No channel data was collected")
            return
        
        logger.info(f"Data collection completed for {len(all_channel_data)} channels")
        
        # Group videos by effective date
        videos_by_date = self.process_videos_by_date(all_channel_data)
        
        if not videos_by_date:
            logger.warning("No video data to process")
            return
        
        # Update data files by date
        self.update_data_files(videos_by_date)
        
        # Create summary file
        logger.info("Generating data summary file...")
        self.file_manager.create_summary_file()
        
        # Completion statistics
        end_time = datetime.now()
        duration = end_time - start_time
        logger.info("=== Data collection completed ===")
        logger.info("="*60)
        logger.info(" YouTube Data Collection Results Summary")
        logger.info("="*60)
        logger.info(f"Execution time:        {duration}")
        logger.info(f"Channels processed:    {self.stats['total_channels_processed']}")
        logger.info(f"Videos processed:      {self.stats['total_videos_processed']}")
        logger.info(f"Live streams detected: {self.stats['total_live_streams']}")
        logger.info(f"Upcoming detected:     {self.stats['total_upcoming']}")
        logger.info(f"Files updated:         {self.stats['total_files_updated']}")
        logger.info(f"Errors:                {len(self.stats['errors'])}")
        if self.stats["errors"]:
            logger.warning("Errors encountered:")
            for error in self.stats["errors"]:
                logger.warning(f"  - {error}")
        logger.info("="*60)
        logger.info(" Youtube Data API v3 Quota Summary")
        logger.info("="*60)
        quota_summary = self.api.quota_tracker.get_summary()
        logger.info(f"quota_used:           {quota_summary['quota_used']}")
        logger.info(f"total_requests:       {quota_summary['total_requests']}")
        logger.info(f"cache_hits:           {quota_summary['cache_hits']}")
        logger.info(f"cache_hit_rate:       {quota_summary['cache_hit_rate']}")
        logger.info(f"requests_by_endpoint: {quota_summary['requests_by_endpoint']}")
        logger.info("="*60)


def main():
    """
    Main function
    """
    # Get API key from environment variable
    api_key = os.getenv("YOUTUBE_API_KEY")
    api_key = "AIzaSyDgfcukaj2knToOExnYpKMs8Ql1XVTyGcg"
    if not api_key:
        logger.error("YOUTUBE_API_KEY is not configured. Please set the environment variable.")
        sys.exit(1)
    
    try:
        # Initialize data collector
        collector = YouTubeDataCollector(api_key)
        
        # Execute data collection
        collector.collect_all_data()
        
    except FileNotFoundError as e:
        logger.error(f"Required file not found: {e}")
        sys.exit(1)
        
    except YouTubeAPIError as e:
        logger.error(f"YouTube API error: {e}")
        sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("Collection interrupted by user")
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
