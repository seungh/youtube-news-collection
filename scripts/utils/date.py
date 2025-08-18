#!/usr/bin/env python3
"""
Date utilities for YouTube data collection
Handles both regular videos and live streams with different time logic
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import re

def parse_youtube_date(date_string):
    """
    Parse YouTube API date string to datetime object.
    Example: '2025-08-16T10:30:00Z' -> datetime object
    """
    try:
        # Parse ISO 8601 format
        if date_string.endswith('Z'):
            date_string = date_string[:-1] + '+00:00'

        return datetime.fromisoformat(date_string)
    except Exception as e:
        raise ValueError(f"Date parsing error: {date_string}, {e}")

def convert_time(date_obj, timezone):
    """
    Convert datetime object to specified time zone.
    """
    if date_obj.tzinfo is None:
        # If naive datetime, assume it's UTC
        date_obj = date_obj.replace(tzinfo=timezone.utc)
    
    # Return converted time
    return date_obj.astimezone(ZoneInfo(timezone))

def get_date_path(date_obj):
    """
    Convert datetime object to year/month/day format for file paths.
    """
    return {
        'year': date_obj.strftime('%Y'),
        'month': date_obj.strftime('%m'), 
        'day': date_obj.strftime('%d')
    }

def format_date_for_filename(date_obj):
    """
    Convert datetime object to date string for filename.
    """
    return date_obj.strftime('%Y-%m-%d')

def parse_duration(duration_str, output_type="formatted"):
    """
    Convert YouTube API duration format (PT10M30S) to seconds.
    """
    if not duration_str:
        return 0
    
    # PT10M30S -> 630 seconds
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)
    
    if not match:
        return 0
    
    hours, minutes, seconds = match.groups()
    
    if output_type == "formatted":
        out = f"{hours:02}:{minutes:02}:{seconds:02}"
    elif output_type == "seconds":
        out = int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
    
    return out

def is_date_in_range(date_obj, start_date=None, end_date=None):
    """
    Check if given date is within specified range.
    """
    if start_date and date_obj < start_date:
        return False
    if end_date and date_obj > end_date:
        return False
    return True

def get_utc_now():
    """
    Return current UTC time.
    """
    return datetime.now(timezone.utc)

def is_live_stream(video_data):
    """
    Determine if a video is a live stream based on video metadata.
    Checks for live broadcast content and streaming indicators.
    """
    # Check if it's a live broadcast
    live_broadcast_content = video_data.get('liveBroadcastContent', 'none')
    if live_broadcast_content in ['live', 'upcoming']:
        return True
    
    # Check duration - live streams often have duration "P0D" or very long durations
    duration = video_data.get('duration', '')
    if duration in ['P0D', 'PT0S'] or not duration:
        return True
    
    return False

def get_effective_date_for_video(video_data, timezone="UTC"):
    """
    Get the effective date for storing the video based on its type.
    For regular videos: use publishedAt
    For live streams: use actualStartTime or scheduledStartTime if available, otherwise publishedAt
    """
    try:
        # Check if it's a live stream
        if video_data["liveStreamingDetails"]:
            # For live streams, prefer actual streaming time
            live_details = video_data.get('liveStreamingDetails', {})
            
            # Try actualStartTime first (when stream actually started)
            if 'actualStartTime' in live_details:
                return convert_time(parse_youtube_date(live_details['actualStartTime']), timezone)
            
            # Try scheduledStartTime (when stream was scheduled to start)
            if 'scheduledStartTime' in live_details:
                return convert_time(parse_youtube_date(live_details['scheduledStartTime']), timezone)
            
            # If no live streaming details, check if it's currently live
            if video_data.get('liveBroadcastContent') == 'live':
                # For currently live streams, use current time
                return convert_time(get_utc_now(), timezone)
        
        # For regular videos or fallback, use publishedAt
        return convert_time(parse_youtube_date(video_data['publishedAt']), timezone)
        
    except Exception as e:
        # Fallback to publishedAt if any error occurs
        return convert_time(parse_youtube_date(video_data['publishedAt']), timezone)