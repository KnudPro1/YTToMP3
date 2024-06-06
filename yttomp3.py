import os
import re
import time
import logging
import ssl
import certifi
import json
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pytube import YouTube, Playlist
from moviepy.editor import AudioFileClip
from tqdm import tqdm
import tkinter as tk
from tkinter import simpledialog, messagebox, ttk
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import atexit
import update_checker  # Import the update checker
import update_checker  # Ensure this import is present

def main():
    # Check for updates before running the application
    update_checker.check_for_update()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/youtube.readonly']

ssl_context = ssl.create_default_context(cafile=certifi.where())

credentials = None  # Global variable to store credentials

def sanitize_folder_name(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip('.')

def get_authenticated_service():
    global credentials
    logger.info("Checking for existing Google API credentials")
    
    # If no credentials or they are invalid, authenticate
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(google.auth.transport.requests.Request())
                logger.info("Google API credentials refreshed")
            except Exception as e:
                logger.warning("Failed to refresh credentials: %s", e)
                credentials = None
        if not credentials:
            logger.info("Initializing Google API authentication")
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            credentials = flow.run_local_server(open_browser=True)
            logger.info("Google API authentication successful")

    return build('youtube', 'v3', credentials=credentials)

def clear_credentials():
    global credentials
    credentials = None
    logger.info("Google API credentials cleared")

# Register cleanup function to run at exit
atexit.register(clear_credentials)

def list_playlists(youtube):
    playlists = []
    request = youtube.playlists().list(part="snippet,contentDetails", mine=True)
    while request:
        response = request.execute()
        playlists.extend(response.get('items', []))
        request = youtube.playlists().list_next(request, response)
    return [(playlist['id'], playlist['snippet']['title']) for playlist in playlists]

def get_playlist_videos(youtube, playlist_id):
    videos = []
    request = youtube.playlistItems().list(part="snippet,contentDetails", maxResults=50, playlistId=playlist_id)
    while request:
        response = request.execute()
        videos.extend(response.get('items', []))
        request = youtube.playlistItems().list_next(request, response)
    return [video['contentDetails']['videoId'] for video in videos]

def download_youtube_video(url, output_folder):
    for attempt in range(3):  # Retry up to 3 times
        try:
            yt = YouTube(url)
            mp3_file = os.path.join(output_folder, re.sub(r'[\\/:*?"<>|]', '_', yt.title) + ".mp3")
            
            if os.path.exists(mp3_file):
                logger.info(f"MP3 file '{os.path.basename(mp3_file)}' already exists. Skipping.")
                return mp3_file, yt.title, yt.author, yt.channel_url, yt.thumbnail_url

            video_stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
            video_file_path = video_stream.download(output_path=output_folder)

            return video_file_path, yt.title, yt.author, yt.channel_url, yt.thumbnail_url

        except Exception as e:
            logger.error(f"Error downloading {url} on attempt {attempt + 1}: {str(e)}")
            time.sleep(2)  # Wait a bit before retrying
    return None, None, None, None, None

def convert_to_mp3(video_file_path, output_folder, artist, channel_url, thumbnail_url):
    for attempt in range(3):  # Retry up to 3 times
        try:
            mp3_file = os.path.join(output_folder, os.path.splitext(os.path.basename(video_file_path))[0] + ".mp3")
            
            if os.path.exists(mp3_file):
                logger.info(f"MP3 file '{os.path.basename(mp3_file)}' already exists. Skipping conversion.")
                return mp3_file

            video_clip = AudioFileClip(video_file_path)
            video_clip.write_audiofile(mp3_file, logger=None)
            video_clip.close()

            # Add metadata and thumbnail (if any)
            return mp3_file

        except Exception as e:
            logger.error(f"Error converting {video_file_path} to MP3 on attempt {attempt + 1}: {str(e)}")
            time.sleep(2)  # Wait a bit before retrying
        finally:
            if video_file_path and os.path.exists(video_file_path):
                os.remove(video_file_path)
    return None

def worker(queue, output_folder, downloaded_videos, skipped_videos, progress_bar):
    while not queue.empty():
        video_url, playlist_title = queue.get()
        try:
            # Create a folder for the playlist if it has a title
            if playlist_title:
                playlist_folder = os.path.join(output_folder, playlist_title)
                os.makedirs(playlist_folder, exist_ok=True)
            else:
                playlist_folder = output_folder

            video_file_path, video_title, artist, channel_url, thumbnail_url = download_youtube_video(video_url, playlist_folder)
            if video_file_path and video_file_path.endswith('.mp4'):
                mp3_file_path = convert_to_mp3(video_file_path, playlist_folder, artist, channel_url, thumbnail_url)
                if mp3_file_path:
                    logger.info(f"Video '{video_title}' downloaded and converted to MP3.")
                    downloaded_videos.append((video_title, video_url))
                else:
                    logger.error(f"Error converting video '{video_title}'.")
                    skipped_videos.append((video_title, video_url, "Conversion error"))
            elif video_file_path and video_file_path.endswith('.mp3'):
                logger.info(f"Video '{video_title}' already converted to MP3. Skipping.")
                downloaded_videos.append((video_title, video_url))
            else:
                logger.error(f"Error downloading video from {video_url}.")
                skipped_videos.append((video_title, video_url, "Download error"))
        except Exception as e:
            logger.error(f"Unexpected error with video {video_url}: {str(e)}")
            skipped_videos.append((None, video_url, "Unexpected error"))
        finally:
            queue.task_done()
            progress_bar.update(1)

def download_youtube_playlist(playlist_url, youtube, output_folder, progress_bar):
    try:
        playlist_id = playlist_url.split("list=")[-1]
        video_ids = get_playlist_videos(youtube, playlist_id)
        if not video_ids:
            logger.warning(f"Playlist '{playlist_url}' contains no videos.")
            return [], []

        request = youtube.playlists().list(part="snippet", id=playlist_id)
        response = request.execute()
        playlist_title = sanitize_folder_name(response['items'][0]['snippet']['title'])
        playlist_folder = os.path.join(output_folder, playlist_title)
        os.makedirs(playlist_folder, exist_ok=True)

        queue = Queue()
        for video_id in video_ids:
            video_url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
            queue.put((video_url, playlist_title))

        downloaded_videos = []
        skipped_videos = []

        logger.info(f"Downloading playlist '{playlist_title}':")

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, queue, playlist_folder, downloaded_videos, skipped_videos, progress_bar) for _ in range(5)]
            for future in futures:
                future.result()

        logger.info("Download complete:")
        logger.info(f"Number of songs downloaded: {len(downloaded_videos)}")

        return downloaded_videos, skipped_videos

    except ssl.SSLError as ssl_error:
        logger.error(f"SSL error encountered: {ssl_error}")
        time.sleep(5)  # Wait before retrying
        return download_youtube_playlist(playlist_url, youtube, output_folder, progress_bar)
    except Exception as e:
        logger.error(f"Error downloading playlist: {str(e)}")
        return [], []

def log_results(downloaded_videos, skipped_videos):
    logs_folder = "logs"
    os.makedirs(logs_folder, exist_ok=True)
    log_file = os.path.join(logs_folder, f"log_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt")

    with open(log_file, 'w', encoding='utf-8') as f:
        f.write("---- Downloaded Videos ----\n")
        for video_title, video_url in downloaded_videos:
            f.write(f"{video_title}: {video_url}\n")

        f.write("\n---- Skipped Videos ----\n")
        for video_title, video_url, skip_reason in skipped_videos:
            f.write(f"{video_title}: {video_url} - Reason: {skip_reason}\n")

        f.write("\n---- Made by KnudPro1 ----\n")

def start_download(youtube_links, youtube):
    mp3_folder = "YouTube MP3 Files"
    os.makedirs(mp3_folder, exist_ok=True)

    total_videos = 0
    for link in youtube_links:
        if "playlist" in link.lower() or "list=" in link:
            playlist_id = link.split("list=")[-1]
            video_ids = get_playlist_videos(youtube, playlist_id)
            total_videos += len(video_ids)
        else:
            total_videos += 1

    with tqdm(total=total_videos, desc="Downloading and converting videos", unit="video") as progress_bar:
        downloaded_videos, skipped_videos = [], []
        queue = Queue()

        for link in youtube_links:
            if "playlist" in link.lower() or "list=" in link:
                playlist_id = link.split("list=")[-1]
                video_ids = get_playlist_videos(youtube, playlist_id)
                request = youtube.playlists().list(part="snippet", id=playlist_id)
                response = request.execute()
                playlist_title = sanitize_folder_name(response['items'][0]['snippet']['title'])
                for video_id in video_ids:
                    video_url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
                    queue.put((video_url, playlist_title))
            else:
                queue.put((link, None))

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, queue, mp3_folder, downloaded_videos, skipped_videos, progress_bar) for _ in range(5)]
            for future in futures:
                future.result()

        log_results(downloaded_videos, skipped_videos)
        messagebox.showinfo("Download Complete", f"Total videos downloaded: {len(downloaded_videos)}\nCheck the logs folder for details.")

def main():
    # Check for updates before running the application
    update_checker.check_for_update()
    
    root = tk.Tk()
    root.withdraw()

    def on_google():
        root.quit()
        root.destroy()
        download_with_google()

    def on_manual():
        root.quit()
        root.destroy()
        download_with_manual()

    root.deiconify()
    root.title("YouTube Downloader")

    label = tk.Label(root, text="Choose the download method:")
    label.pack(pady=10)

    google_button = tk.Button(root, text="Google", command=on_google)
    google_button.pack(pady=5)

    manual_button = tk.Button(root, text="Manual", command=on_manual)
    manual_button.pack(pady=5)

    root.mainloop()

def download_with_google():
    logger.info("Google account selected for playlist access")
    youtube = get_authenticated_service()
    playlists = list_playlists(youtube)

    choices = [f"{title} (ID: {id})" for id, title in playlists]
    choice_str = '\n'.join(f"{i+1}. {choice}" for i, choice in enumerate(choices))
    selected_indices = simpledialog.askstring("YouTube Playlists", f"Select playlists by number (comma separated):\n{choice_str}")

    if selected_indices:
        selected_indices = [int(i.strip()) - 1 for i in selected_indices.split(',')]
        selected_playlist_ids = [playlists[i][0] for i in selected_indices]

        youtube_links = []
        for playlist_id in selected_playlist_ids:
            video_ids = get_playlist_videos(youtube, playlist_id)
            youtube_links.extend([f"https://www.youtube.com/watch?v={vid}&list={playlist_id}" for vid in video_ids])

        if youtube_links:
            start_download(youtube_links, youtube)
        else:
            messagebox.showinfo("YouTube Downloader", "No playlists selected. Exiting.")

def download_with_manual():
    logger.info("Manual input selected for YouTube links")
    links = simpledialog.askstring("YouTube Downloader", "Enter YouTube links (comma separated):")
    if links:
        youtube_links = [link.strip() for link in links.split(',')]
        if youtube_links:
            youtube = get_authenticated_service()
            start_download(youtube_links, youtube)
        else:
            messagebox.showinfo("YouTube Downloader", "No links entered. Exiting.")

if __name__ == "__main__":
    main()
