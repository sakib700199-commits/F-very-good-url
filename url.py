#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║              GOD LEVEL MEDIA GALLERY v2.0                       ║
║         Production-Ready • Mobile-First • Pinterest UI          ║
║     Handles 10 Lakh+ URLs • PostgreSQL • Admin Dashboard        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import re
import csv
import io
import math
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, request, redirect, url_for, session, jsonify,
    render_template_string, flash, abort, Response
)
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2 import pool

# ═══════════════════════════════════════════════════════════════
# APP CONFIGURATION
# ═══════════════════════════════════════════════════════════════

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=12)

DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:password@localhost:5432/mediagallery'
)

# Fix Render's postgres:// -> postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD_HASH = generate_password_hash(
    os.environ.get('ADMIN_PASSWORD', 'admin123')
)

ITEMS_PER_PAGE = 20

# ═══════════════════════════════════════════════════════════════
# DATABASE CONNECTION POOL
# ═══════════════════════════════════════════════════════════════

db_pool = None

def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = psycopg2.pool.ThreadedConnectionPool(
            2, 20, DATABASE_URL
        )
    return db_pool

def get_db():
    conn = get_pool().getconn()
    conn.autocommit = False
    return conn

def put_db(conn):
    get_pool().putconn(conn)

# ═══════════════════════════════════════════════════════════════
# DATABASE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS media_items (
                id BIGSERIAL PRIMARY KEY,
                title VARCHAR(500) NOT NULL,
                url TEXT NOT NULL,
                media_type VARCHAR(20) NOT NULL DEFAULT 'video',
                category VARCHAR(200) NOT NULL DEFAULT 'Uncategorized',
                thumbnail TEXT,
                duration VARCHAR(20),
                description TEXT,
                tags TEXT,
                view_count BIGINT DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(media_type);
            CREATE INDEX IF NOT EXISTS idx_category ON media_items(category);
            CREATE INDEX IF NOT EXISTS idx_active ON media_items(is_active);
            CREATE INDEX IF NOT EXISTS idx_created ON media_items(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_views ON media_items(view_count DESC);
            CREATE INDEX IF NOT EXISTS idx_title_search ON media_items
                USING gin(to_tsvector('english', title));

            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name VARCHAR(200) UNIQUE NOT NULL,
                icon VARCHAR(50) DEFAULT '📁',
                color VARCHAR(7) DEFAULT '#6366f1',
                item_count INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id BIGSERIAL PRIMARY KEY,
                media_id BIGINT REFERENCES media_items(id) ON DELETE CASCADE,
                action VARCHAR(50),
                ip_address VARCHAR(50),
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO categories (name, icon, color) VALUES
                ('Uncategorized', '📁', '#6366f1'),
                ('Movies', '🎬', '#ef4444'),
                ('Music', '🎵', '#22c55e'),
                ('Education', '📚', '#3b82f6'),
                ('Gaming', '🎮', '#a855f7'),
                ('Photography', '📷', '#f59e0b'),
                ('Podcast', '🎙️', '#ec4899'),
                ('Sports', '⚽', '#14b8a6')
            ON CONFLICT (name) DO NOTHING;
        """)
        conn.commit()
        print("✅ Database initialized successfully!")
    except Exception as e:
        conn.rollback()
        print(f"❌ Database init error: {e}")
    finally:
        cur.close()
        put_db(conn)

# ═══════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def detect_media_type(url):
    url_lower = url.lower()
    video_ext = ['.mp4', '.webm', '.ogg', '.m3u8', '.mov', '.avi', '.mkv', '.flv']
    audio_ext = ['.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a', '.wma']
    image_ext = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico']

    video_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com',
                     'twitch.tv', 'streamable.com', 'rumble.com']
    audio_domains = ['soundcloud.com', 'spotify.com', 'audius.co']

    for ext in video_ext:
        if url_lower.endswith(ext):
            return 'video'
    for ext in audio_ext:
        if url_lower.endswith(ext):
            return 'audio'
    for ext in image_ext:
        if url_lower.endswith(ext):
            return 'image'

    parsed = urlparse(url_lower)
    domain = parsed.netloc.replace('www.', '')
    for vd in video_domains:
        if vd in domain:
            return 'video'
    for ad in audio_domains:
        if ad in domain:
            return 'audio'

    return 'video'


def get_embed_url(url):
    """Convert video URLs to embeddable format"""
    if 'youtube.com/watch' in url:
        video_id = re.search(r'v=([a-zA-Z0-9_-]+)', url)
        if video_id:
            return f'https://www.youtube.com/embed/{video_id.group(1)}'
    elif 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[-1].split('?')[0]
        return f'https://www.youtube.com/embed/{video_id}'
    elif 'vimeo.com/' in url:
        video_id = url.split('vimeo.com/')[-1].split('?')[0]
        return f'https://player.vimeo.com/video/{video_id}'
    elif 'dailymotion.com/video/' in url:
        video_id = url.split('/video/')[-1].split('?')[0]
        return f'https://www.dailymotion.com/embed/video/{video_id}'
    return url


def get_youtube_thumbnail(url):
    """Extract YouTube thumbnail"""
    video_id = None
    if 'youtube.com/watch' in url:
        match = re.search(r'v=([a-zA-Z0-9_-]+)', url)
        if match:
            video_id = match.group(1)
    elif 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[-1].split('?')[0]
    if video_id:
        return f'https://img.youtube.com/vi/{video_id}/hqdefault.jpg'
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ═══════════════════════════════════════════════════════════════
# BASE HTML TEMPLATE
# ═══════════════════════════════════════════════════════════════

BASE_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    :root {
        --bg-primary: #0a0a0f;
        --bg-secondary: #12121a;
        --bg-card: #1a1a2e;
        --bg-card-hover: #222240;
        --accent: #6366f1;
        --accent-light: #818cf8;
        --accent-glow: rgba(99,102,241,0.3);
        --text-primary: #f1f5f9;
        --text-secondary: #94a3b8;
        --text-muted: #64748b;
        --border: #2a2a3e;
        --success: #22c55e;
        --danger: #ef4444;
        --warning: #f59e0b;
        --gradient-1: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
        --gradient-2: linear-gradient(135deg, #3b82f6 0%, #6366f1 100%);
        --shadow-card: 0 4px 24px rgba(0,0,0,0.4);
        --shadow-glow: 0 0 40px rgba(99,102,241,0.15);
        --radius: 16px;
        --radius-sm: 10px;
        --radius-full: 9999px;
    }

    * { margin:0; padding:0; box-sizing:border-box; }

    body {
        font-family: 'Inter', -apple-system, sans-serif;
        background: var(--bg-primary);
        color: var(--text-primary);
        min-height: 100vh;
        overflow-x: hidden;
        -webkit-font-smoothing: antialiased;
    }

    /* ===== SCROLLBAR ===== */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg-primary); }
    ::-webkit-scrollbar-thumb { background: var(--accent); border-radius: 10px; }

    /* ===== TOP NAVBAR ===== */
    .top-nav {
        position: fixed; top:0; left:0; right:0; z-index:1000;
        background: rgba(10,10,15,0.85);
        backdrop-filter: blur(20px) saturate(180%);
        -webkit-backdrop-filter: blur(20px) saturate(180%);
        border-bottom: 1px solid rgba(99,102,241,0.1);
        padding: 0 20px;
        height: 64px;
        display: flex; align-items: center; justify-content: space-between;
    }
    .nav-logo {
        font-size: 1.4rem; font-weight: 800;
        background: var(--gradient-1); -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        display: flex; align-items: center; gap: 8px;
        text-decoration: none;
    }
    .nav-logo span { font-size: 1.6rem; }
    .nav-actions { display: flex; gap: 10px; align-items: center; }
    .nav-btn {
        background: var(--bg-card); border: 1px solid var(--border);
        color: var(--text-primary); padding: 8px 16px;
        border-radius: var(--radius-full); font-size: 0.85rem;
        cursor: pointer; transition: all 0.3s;
        text-decoration: none; display: flex; align-items: center; gap: 6px;
        font-weight: 500;
    }
    .nav-btn:hover { background: var(--accent); border-color: var(--accent); transform: translateY(-1px); }
    .nav-btn.active { background: var(--accent); border-color: var(--accent); }

    /* ===== SEARCH BAR ===== */
    .search-container {
        position: sticky; top: 64px; z-index: 999;
        background: rgba(10,10,15,0.9);
        backdrop-filter: blur(20px);
        padding: 16px 20px;
        border-bottom: 1px solid var(--border);
    }
    .search-wrapper {
        max-width: 700px; margin: 0 auto;
        position: relative;
    }
    .search-input {
        width: 100%; padding: 14px 50px 14px 48px;
        background: var(--bg-card); border: 2px solid var(--border);
        border-radius: var(--radius-full); color: var(--text-primary);
        font-size: 1rem; font-family: 'Inter', sans-serif;
        transition: all 0.3s; outline: none;
    }
    .search-input:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 4px var(--accent-glow);
    }
    .search-input::placeholder { color: var(--text-muted); }
    .search-icon {
        position: absolute; left: 18px; top: 50%; transform: translateY(-50%);
        color: var(--text-muted); font-size: 1.1rem;
    }
    .search-clear {
        position: absolute; right: 16px; top: 50%; transform: translateY(-50%);
        background: none; border: none; color: var(--text-muted);
        cursor: pointer; font-size: 1.2rem; display: none;
    }

    /* ===== FILTER CHIPS ===== */
    .filter-bar {
        padding: 12px 20px;
        overflow-x: auto; white-space: nowrap;
        -ms-overflow-style: none; scrollbar-width: none;
        display: flex; gap: 8px;
    }
    .filter-bar::-webkit-scrollbar { display: none; }
    .filter-chip {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 8px 18px; border-radius: var(--radius-full);
        background: var(--bg-card); border: 1px solid var(--border);
        color: var(--text-secondary); font-size: 0.85rem;
        cursor: pointer; transition: all 0.3s; flex-shrink: 0;
        text-decoration: none; font-weight: 500;
    }
    .filter-chip:hover, .filter-chip.active {
        background: var(--accent); color: white;
        border-color: var(--accent); transform: scale(1.02);
    }
    .filter-chip .count {
        background: rgba(255,255,255,0.2); padding: 2px 8px;
        border-radius: var(--radius-full); font-size: 0.75rem;
    }

    /* ===== MASONRY GRID ===== */
    .content-area {
        padding: 20px;
        padding-bottom: 100px;
        max-width: 1400px;
        margin: 0 auto;
    }
    .masonry-grid {
        columns: 2;
        column-gap: 14px;
    }
    @media(min-width: 768px) { .masonry-grid { columns: 3; column-gap: 18px; } }
    @media(min-width: 1024px) { .masonry-grid { columns: 4; column-gap: 20px; } }
    @media(min-width: 1280px) { .masonry-grid { columns: 5; column-gap: 20px; } }

    /* ===== MEDIA CARD ===== */
    .media-card {
        break-inside: avoid;
        margin-bottom: 14px;
        background: var(--bg-card);
        border-radius: var(--radius);
        overflow: hidden;
        border: 1px solid var(--border);
        transition: all 0.4s cubic-bezier(0.4,0,0.2,1);
        cursor: pointer;
        position: relative;
    }
    .media-card:hover {
        transform: translateY(-4px);
        box-shadow: var(--shadow-glow);
        border-color: var(--accent);
    }
    .card-thumb {
        position: relative;
        overflow: hidden;
        background: var(--bg-secondary);
    }
    .card-thumb img {
        width: 100%; display: block;
        transition: transform 0.5s;
        object-fit: cover;
    }
    .media-card:hover .card-thumb img { transform: scale(1.08); }
    .card-thumb .play-overlay {
        position: absolute; inset: 0;
        display: flex; align-items: center; justify-content: center;
        background: rgba(0,0,0,0.3);
        opacity: 0; transition: opacity 0.3s;
    }
    .media-card:hover .play-overlay { opacity: 1; }
    .play-btn {
        width: 56px; height: 56px; border-radius: 50%;
        background: var(--accent); display: flex;
        align-items: center; justify-content: center;
        box-shadow: 0 4px 20px var(--accent-glow);
        transition: transform 0.3s;
    }
    .play-btn:hover { transform: scale(1.15); }
    .play-btn svg { width: 24px; height: 24px; fill: white; margin-left: 3px; }

    .type-badge {
        position: absolute; top: 10px; left: 10px;
        padding: 4px 10px; border-radius: var(--radius-full);
        font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .type-video { background: rgba(239,68,68,0.9); color: white; }
    .type-audio { background: rgba(34,197,94,0.9); color: white; }
    .type-image { background: rgba(59,130,246,0.9); color: white; }

    .duration-badge {
        position: absolute; bottom: 10px; right: 10px;
        padding: 3px 8px; border-radius: 6px;
        background: rgba(0,0,0,0.8); color: white;
        font-size: 0.75rem; font-weight: 600;
    }

    .card-body { padding: 14px; }
    .card-title {
        font-size: 0.9rem; font-weight: 600;
        color: var(--text-primary);
        line-height: 1.4;
        display: -webkit-box; -webkit-line-clamp: 2;
        -webkit-box-orient: vertical; overflow: hidden;
        margin-bottom: 8px;
    }
    .card-meta {
        display: flex; align-items: center; justify-content: space-between;
        font-size: 0.78rem; color: var(--text-muted);
    }
    .card-category {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 3px 10px; border-radius: var(--radius-full);
        background: rgba(99,102,241,0.1); color: var(--accent-light);
        font-size: 0.72rem; font-weight: 500;
    }
    .card-views { display: flex; align-items: center; gap: 4px; }

    /* ===== NO THUMBNAIL PLACEHOLDER ===== */
    .no-thumb {
        height: 180px; display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        background: linear-gradient(135deg, var(--bg-secondary), var(--bg-card));
        font-size: 3rem;
    }
    .no-thumb.audio-placeholder { background: linear-gradient(135deg, #064e3b, #065f46); }
    .no-thumb.video-placeholder { background: linear-gradient(135deg, #450a0a, #7f1d1d); }
    .no-thumb.image-placeholder { background: linear-gradient(135deg, #1e3a5f, #1e40af); }

    /* ===== MODAL / OVERLAY ===== */
    .modal-overlay {
        position: fixed; inset: 0; z-index: 2000;
        background: rgba(0,0,0,0.92);
        backdrop-filter: blur(10px);
        display: none; align-items: center; justify-content: center;
        padding: 20px;
    }
    .modal-overlay.active { display: flex; }
    .modal-content {
        width: 100%; max-width: 900px;
        background: var(--bg-secondary);
        border-radius: var(--radius);
        overflow: hidden;
        border: 1px solid var(--border);
        max-height: 90vh; overflow-y: auto;
        animation: modalIn 0.3s ease;
    }
    @keyframes modalIn {
        from { opacity:0; transform: scale(0.9) translateY(20px); }
        to { opacity:1; transform: scale(1) translateY(0); }
    }
    .modal-close {
        position: absolute; top: 16px; right: 16px;
        width: 44px; height: 44px; border-radius: 50%;
        background: rgba(255,255,255,0.1); border: none;
        color: white; font-size: 1.4rem; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        transition: all 0.3s; z-index: 10;
    }
    .modal-close:hover { background: var(--danger); transform: rotate(90deg); }
    .modal-player {
        width: 100%; aspect-ratio: 16/9;
        background: black;
    }
    .modal-player iframe,
    .modal-player video,
    .modal-player audio {
        width: 100%; height: 100%; border: none;
    }
    .modal-player img {
        width: 100%; height: 100%; object-fit: contain;
    }
    .modal-info { padding: 20px; }
    .modal-title { font-size: 1.2rem; font-weight: 700; margin-bottom: 8px; }
    .modal-meta { color: var(--text-muted); font-size: 0.9rem; }

    /* ===== AUDIO PLAYER CARD ===== */
    .audio-wave {
        display: flex; align-items: end; gap: 3px;
        height: 60px; padding: 20px;
    }
    .audio-wave .bar {
        width: 4px; border-radius: 2px;
        background: var(--accent); animation: wave 1.2s ease-in-out infinite;
    }
    .audio-wave .bar:nth-child(1) { height: 30%; animation-delay: 0s; }
    .audio-wave .bar:nth-child(2) { height: 60%; animation-delay: 0.1s; }
    .audio-wave .bar:nth-child(3) { height: 40%; animation-delay: 0.2s; }
    .audio-wave .bar:nth-child(4) { height: 80%; animation-delay: 0.3s; }
    .audio-wave .bar:nth-child(5) { height: 50%; animation-delay: 0.4s; }
    .audio-wave .bar:nth-child(6) { height: 70%; animation-delay: 0.5s; }
    .audio-wave .bar:nth-child(7) { height: 35%; animation-delay: 0.6s; }
    @keyframes wave {
        0%,100% { transform: scaleY(1); }
        50% { transform: scaleY(0.4); }
    }

    /* ===== PAGINATION ===== */
    .pagination {
        display: flex; justify-content: center; gap: 6px;
        padding: 30px 20px; flex-wrap: wrap;
    }
    .page-btn {
        min-width: 42px; height: 42px;
        border-radius: var(--radius-sm);
        background: var(--bg-card); border: 1px solid var(--border);
        color: var(--text-secondary); font-size: 0.9rem;
        cursor: pointer; transition: all 0.3s;
        display: flex; align-items: center; justify-content: center;
        text-decoration: none; font-weight: 500;
    }
    .page-btn:hover, .page-btn.active {
        background: var(--accent); color: white;
        border-color: var(--accent);
    }
    .page-btn.disabled { opacity: 0.4; pointer-events: none; }

    /* ===== BOTTOM NAV (Mobile) ===== */
    .bottom-nav {
        position: fixed; bottom: 0; left: 0; right: 0; z-index: 1000;
        background: rgba(10,10,15,0.95);
        backdrop-filter: blur(20px);
        border-top: 1px solid var(--border);
        display: flex; justify-content: space-around;
        padding: 8px 0 max(8px, env(safe-area-inset-bottom));
    }
    @media(min-width: 768px) { .bottom-nav { display: none; } }
    .bnav-item {
        display: flex; flex-direction: column; align-items: center;
        gap: 3px; color: var(--text-muted); text-decoration: none;
        font-size: 0.68rem; font-weight: 500; padding: 4px 12px;
        transition: color 0.3s; cursor: pointer;
    }
    .bnav-item.active, .bnav-item:hover { color: var(--accent-light); }
    .bnav-item svg { width: 22px; height: 22px; }

    /* ===== STATS BAR ===== */
    .stats-bar {
        display: flex; gap: 12px; padding: 0 20px 10px;
        overflow-x: auto; white-space: nowrap;
        -ms-overflow-style: none; scrollbar-width: none;
    }
    .stats-bar::-webkit-scrollbar { display: none; }
    .stat-pill {
        display: flex; align-items: center; gap: 8px;
        padding: 10px 18px; border-radius: var(--radius);
        background: var(--bg-card); border: 1px solid var(--border);
        flex-shrink: 0;
    }
    .stat-number { font-size: 1.2rem; font-weight: 800; color: var(--accent-light); }
    .stat-label { font-size: 0.78rem; color: var(--text-muted); }

    /* ===== TOAST NOTIFICATIONS ===== */
    .toast-container {
        position: fixed; top: 80px; right: 20px; z-index: 3000;
        display: flex; flex-direction: column; gap: 10px;
    }
    .toast {
        padding: 14px 20px; border-radius: var(--radius-sm);
        background: var(--bg-card); border: 1px solid var(--border);
        color: var(--text-primary); font-size: 0.9rem;
        box-shadow: var(--shadow-card);
        animation: toastIn 0.3s ease, toastOut 0.3s ease 2.7s forwards;
        display: flex; align-items: center; gap: 10px;
        max-width: 350px;
    }
    .toast.success { border-left: 4px solid var(--success); }
    .toast.error { border-left: 4px solid var(--danger); }
    .toast.info { border-left: 4px solid var(--accent); }
    @keyframes toastIn { from { opacity:0; transform: translateX(100px); } to { opacity:1; transform: translateX(0); } }
    @keyframes toastOut { from { opacity:1; } to { opacity:0; transform: translateX(100px); } }

    /* ===== ADMIN STYLES ===== */
    .admin-container {
        max-width: 1100px; margin: 80px auto 100px;
        padding: 0 20px;
    }
    .admin-header {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 24px; flex-wrap: wrap; gap: 12px;
    }
    .admin-title {
        font-size: 1.6rem; font-weight: 800;
        background: var(--gradient-1); -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .admin-card {
        background: var(--bg-card); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 24px;
        margin-bottom: 20px;
    }
    .admin-card h3 {
        font-size: 1.1rem; font-weight: 700; margin-bottom: 16px;
        display: flex; align-items: center; gap: 8px;
    }
    .form-group { margin-bottom: 16px; }
    .form-label {
        display: block; margin-bottom: 6px;
        font-size: 0.85rem; font-weight: 600;
        color: var(--text-secondary);
    }
    .form-input, .form-select, .form-textarea {
        width: 100%; padding: 12px 16px;
        background: var(--bg-secondary); border: 2px solid var(--border);
        border-radius: var(--radius-sm); color: var(--text-primary);
        font-size: 0.95rem; font-family: 'Inter', sans-serif;
        transition: all 0.3s; outline: none;
    }
    .form-input:focus, .form-select:focus, .form-textarea:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 4px var(--accent-glow);
    }
    .form-textarea { min-height: 100px; resize: vertical; }
    .form-select option { background: var(--bg-secondary); }

    .btn {
        display: inline-flex; align-items: center; gap: 8px;
        padding: 12px 24px; border-radius: var(--radius-sm);
        font-size: 0.9rem; font-weight: 600; cursor: pointer;
        transition: all 0.3s; border: none;
        font-family: 'Inter', sans-serif;
        text-decoration: none;
    }
    .btn-primary { background: var(--gradient-1); color: white; }
    .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 25px var(--accent-glow); }
    .btn-danger { background: var(--danger); color: white; }
    .btn-danger:hover { background: #dc2626; }
    .btn-secondary { background: var(--bg-secondary); color: var(--text-primary); border: 1px solid var(--border); }
    .btn-success { background: var(--success); color: white; }
    .btn-sm { padding: 8px 14px; font-size: 0.8rem; }
    .btn-block { width: 100%; justify-content: center; }

    .admin-table {
        width: 100%; border-collapse: collapse;
        font-size: 0.85rem;
    }
    .admin-table th {
        padding: 12px; text-align: left;
        background: var(--bg-secondary);
        color: var(--text-muted); font-weight: 600;
        border-bottom: 2px solid var(--border);
        position: sticky; top: 0;
    }
    .admin-table td {
        padding: 12px; border-bottom: 1px solid var(--border);
        vertical-align: middle;
    }
    .admin-table tr:hover { background: rgba(99,102,241,0.05); }
    .admin-table .title-cell {
        max-width: 250px; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap;
    }
    .table-container {
        overflow-x: auto; border-radius: var(--radius-sm);
        border: 1px solid var(--border);
    }

    .toggle-switch {
        position: relative; width: 44px; height: 24px;
    }
    .toggle-switch input { display: none; }
    .toggle-slider {
        position: absolute; inset: 0; background: var(--bg-secondary);
        border-radius: 12px; cursor: pointer; transition: 0.3s;
        border: 2px solid var(--border);
    }
    .toggle-slider::before {
        content: ''; position: absolute; width: 16px; height: 16px;
        border-radius: 50%; background: var(--text-muted);
        top: 2px; left: 2px; transition: 0.3s;
    }
    .toggle-switch input:checked + .toggle-slider {
        background: var(--accent); border-color: var(--accent);
    }
    .toggle-switch input:checked + .toggle-slider::before {
        transform: translateX(20px); background: white;
    }

    /* ===== LOGIN PAGE ===== */
    .login-container {
        min-height: 100vh; display: flex;
        align-items: center; justify-content: center;
        padding: 20px;
        background: radial-gradient(ellipse at top, rgba(99,102,241,0.1), transparent 60%);
    }
    .login-card {
        width: 100%; max-width: 400px;
        background: var(--bg-card); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 40px 32px;
        box-shadow: var(--shadow-card);
    }
    .login-card h1 {
        text-align: center; font-size: 1.8rem; font-weight: 800;
        margin-bottom: 8px;
        background: var(--gradient-1); -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .login-card .subtitle {
        text-align: center; color: var(--text-muted);
        margin-bottom: 32px; font-size: 0.9rem;
    }
    .login-error {
        background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3);
        color: #fca5a5; padding: 12px; border-radius: var(--radius-sm);
        margin-bottom: 16px; font-size: 0.85rem; text-align: center;
    }

    /* ===== EMPTY STATE ===== */
    .empty-state {
        text-align: center; padding: 60px 20px;
    }
    .empty-state .icon { font-size: 4rem; margin-bottom: 16px; }
    .empty-state h3 { font-size: 1.3rem; color: var(--text-secondary); margin-bottom: 8px; }
    .empty-state p { color: var(--text-muted); font-size: 0.9rem; }

    /* ===== LOADING SKELETON ===== */
    .skeleton {
        background: linear-gradient(90deg, var(--bg-card) 25%, var(--bg-card-hover) 50%, var(--bg-card) 75%);
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
        border-radius: var(--radius);
    }
    @keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }

    /* ===== RESPONSIVE ===== */
    @media(max-width: 640px) {
        .top-nav { padding: 0 14px; height: 56px; }
        .nav-logo { font-size: 1.15rem; }
        .search-container { padding: 10px 14px; }
        .search-input { padding: 12px 44px 12px 42px; font-size: 0.9rem; }
        .content-area { padding: 14px; }
        .masonry-grid { column-gap: 10px; }
        .media-card { margin-bottom: 10px; }
        .card-body { padding: 10px; }
        .card-title { font-size: 0.82rem; }
        .admin-container { margin: 68px auto 100px; padding: 0 14px; }
        .admin-card { padding: 16px; }
        .admin-table { font-size: 0.78rem; }
        .admin-table th, .admin-table td { padding: 8px; }
    }

    /* Bulk upload drag area */
    .drag-area {
        border: 2px dashed var(--border); border-radius: var(--radius);
        padding: 40px; text-align: center;
        transition: all 0.3s; cursor: pointer;
    }
    .drag-area:hover, .drag-area.dragover {
        border-color: var(--accent);
        background: rgba(99,102,241,0.05);
    }
    .drag-area .icon { font-size: 3rem; margin-bottom: 12px; }
    .drag-area p { color: var(--text-muted); }

    /* ===== GRID STATS (Admin Dashboard) ===== */
    .stats-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 14px; margin-bottom: 24px;
    }
    .stat-card {
        background: var(--bg-card); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 20px;
        display: flex; align-items: center; gap: 14px;
    }
    .stat-icon {
        width: 48px; height: 48px; border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
        font-size: 1.4rem;
    }
    .stat-card .stat-number { font-size: 1.5rem; font-weight: 800; }
    .stat-card .stat-label { font-size: 0.8rem; color: var(--text-muted); }

    /* File upload info */
    .upload-info {
        background: rgba(59,130,246,0.1);
        border: 1px solid rgba(59,130,246,0.2);
        border-radius: var(--radius-sm);
        padding: 12px 16px;
        font-size: 0.82rem;
        color: var(--text-secondary);
        margin-top: 12px;
    }
    .upload-info code {
        background: rgba(0,0,0,0.3);
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 0.8rem;
    }

    .inline-flex { display: inline-flex; align-items: center; gap: 6px; }
    .ml-auto { margin-left: auto; }
    .text-center { text-align: center; }
    .mt-2 { margin-top: 8px; }
    .mt-4 { margin-top: 16px; }
    .mb-2 { margin-bottom: 8px; }
    .gap-2 { gap: 8px; }
    .flex { display: flex; }
    .flex-wrap { flex-wrap: wrap; }
    .items-center { align-items: center; }
    .justify-between { justify-content: space-between; }
</style>
"""

# ═══════════════════════════════════════════════════════════════
# SVG ICONS
# ═══════════════════════════════════════════════════════════════

ICONS = {
    'play': '<svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
    'search': '🔍',
    'home': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
    'grid': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    'settings': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/></svg>',
    'user': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
}

# ═══════════════════════════════════════════════════════════════
# PUBLIC PAGES
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('q', '').strip()
    media_type = request.args.get('type', '').strip()
    category = request.args.get('category', '').strip()
    sort = request.args.get('sort', 'newest').strip()

    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Build query
        conditions = ["is_active = TRUE"]
        params = []

        if search:
            conditions.append(
                "(to_tsvector('english', title) @@ plainto_tsquery('english', %s) "
                "OR title ILIKE %s OR tags ILIKE %s)"
            )
            params.extend([search, f'%{search}%', f'%{search}%'])

        if media_type:
            conditions.append("media_type = %s")
            params.append(media_type)

        if category:
            conditions.append("category = %s")
            params.append(category)

        where = " AND ".join(conditions)

        # Sort
        sort_map = {
            'newest': 'created_at DESC',
            'oldest': 'created_at ASC',
            'popular': 'view_count DESC',
            'title': 'title ASC',
        }
        order = sort_map.get(sort, 'created_at DESC')

        # Count
        cur.execute(f"SELECT COUNT(*) as total FROM media_items WHERE {where}", params)
        total = cur.fetchone()['total']
        total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))
        page = max(1, min(page, total_pages))

        offset = (page - 1) * ITEMS_PER_PAGE
        cur.execute(
            f"SELECT * FROM media_items WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
            params + [ITEMS_PER_PAGE, offset]
        )
        items = cur.fetchall()

        # Categories
        cur.execute("""
            SELECT c.name, c.icon, c.color,
                   COUNT(m.id) as count
            FROM categories c
            LEFT JOIN media_items m ON m.category = c.name AND m.is_active = TRUE
            GROUP BY c.name, c.icon, c.color
            ORDER BY count DESC
        """)
        categories = cur.fetchall()

        # Stats
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE is_active) as total_active,
                COUNT(*) FILTER (WHERE media_type='video' AND is_active) as videos,
                COUNT(*) FILTER (WHERE media_type='audio' AND is_active) as audios,
                COUNT(*) FILTER (WHERE media_type='image' AND is_active) as images,
                COALESCE(SUM(view_count) FILTER (WHERE is_active), 0) as total_views
            FROM media_items
        """)
        stats = cur.fetchone()

    finally:
        cur.close()
        put_db(conn)

    return render_template_string(INDEX_TEMPLATE,
        items=items, page=page, total_pages=total_pages,
        total=total, search=search, media_type=media_type,
        category=category, sort=sort, categories=categories,
        stats=stats, get_embed_url=get_embed_url,
        get_youtube_thumbnail=get_youtube_thumbnail
    )


@app.route('/api/view/<int:item_id>', methods=['POST'])
def track_view(item_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE media_items SET view_count = view_count + 1 WHERE id = %s", (item_id,))
        conn.commit()
    except:
        conn.rollback()
    finally:
        cur.close()
        put_db(conn)
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session.permanent = True
            session['admin_logged_in'] = True
            flash('Welcome back, Admin! 🎉', 'success')
            return redirect(url_for('admin_dashboard'))
        error = 'Invalid credentials. Try again.'
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Stats
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE is_active) as active,
                COUNT(*) FILTER (WHERE NOT is_active) as inactive,
                COUNT(*) FILTER (WHERE media_type='video') as videos,
                COUNT(*) FILTER (WHERE media_type='audio') as audios,
                COUNT(*) FILTER (WHERE media_type='image') as images,
                COALESCE(SUM(view_count), 0) as total_views,
                COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as today
            FROM media_items
        """)
        stats = cur.fetchone()

        # Recent items
        page = request.args.get('page', 1, type=int)
        search = request.args.get('q', '').strip()

        conditions = ["1=1"]
        params = []
        if search:
            conditions.append("(title ILIKE %s OR url ILIKE %s OR category ILIKE %s)")
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        where = " AND ".join(conditions)

        cur.execute(f"SELECT COUNT(*) as total FROM media_items WHERE {where}", params)
        total = cur.fetchone()['total']
        total_pages = max(1, math.ceil(total / 50))
        page = max(1, min(page, total_pages))

        cur.execute(
            f"SELECT * FROM media_items WHERE {where} ORDER BY created_at DESC LIMIT 50 OFFSET %s",
            params + [(page - 1) * 50]
        )
        items = cur.fetchall()

        # Categories
        cur.execute("SELECT * FROM categories ORDER BY name")
        categories = cur.fetchall()

    finally:
        cur.close()
        put_db(conn)

    return render_template_string(ADMIN_TEMPLATE,
        stats=stats, items=items, categories=categories,
        page=page, total_pages=total_pages, total=total,
        search=search
    )


@app.route('/admin/add', methods=['POST'])
@login_required
def admin_add():
    title = request.form.get('title', '').strip()
    url_val = request.form.get('url', '').strip()
    media_type = request.form.get('media_type', '').strip()
    category = request.form.get('category', 'Uncategorized').strip()
    thumbnail = request.form.get('thumbnail', '').strip()
    duration = request.form.get('duration', '').strip()
    description = request.form.get('description', '').strip()
    tags = request.form.get('tags', '').strip()

    if not title or not url_val:
        flash('Title and URL are required!', 'error')
        return redirect(url_for('admin_dashboard'))

    if not media_type:
        media_type = detect_media_type(url_val)

    if not thumbnail:
        thumbnail = get_youtube_thumbnail(url_val)

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO media_items (title, url, media_type, category, thumbnail, duration, description, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (title, url_val, media_type, category, thumbnail, duration, description, tags))

        # Update category count
        cur.execute("""
            INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING
        """, (category,))

        conn.commit()
        flash(f'"{title}" added successfully! ✅', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/edit/<int:item_id>', methods=['GET', 'POST'])
@login_required
def admin_edit(item_id):
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            url_val = request.form.get('url', '').strip()
            media_type = request.form.get('media_type', 'video').strip()
            category = request.form.get('category', 'Uncategorized').strip()
            thumbnail = request.form.get('thumbnail', '').strip()
            duration = request.form.get('duration', '').strip()
            description = request.form.get('description', '').strip()
            tags = request.form.get('tags', '').strip()
            is_active = request.form.get('is_active') == 'on'

            cur_w = conn.cursor()
            cur_w.execute("""
                UPDATE media_items SET
                    title=%s, url=%s, media_type=%s, category=%s,
                    thumbnail=%s, duration=%s, description=%s, tags=%s,
                    is_active=%s, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
            """, (title, url_val, media_type, category, thumbnail,
                  duration, description, tags, is_active, item_id))
            conn.commit()
            cur_w.close()
            flash(f'Updated successfully! ✅', 'success')
            return redirect(url_for('admin_dashboard'))

        cur.execute("SELECT * FROM media_items WHERE id = %s", (item_id,))
        item = cur.fetchone()
        if not item:
            abort(404)

        cur.execute("SELECT * FROM categories ORDER BY name")
        categories = cur.fetchall()

    finally:
        cur.close()
        put_db(conn)

    return render_template_string(EDIT_TEMPLATE, item=item, categories=categories)


@app.route('/admin/delete/<int:item_id>', methods=['POST'])
@login_required
def admin_delete(item_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM media_items WHERE id = %s", (item_id,))
        conn.commit()
        flash('Item deleted! 🗑️', 'info')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/toggle/<int:item_id>', methods=['POST'])
@login_required
def admin_toggle(item_id):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE media_items SET is_active = NOT is_active, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING is_active",
            (item_id,)
        )
        result = cur.fetchone()
        conn.commit()
        status = "activated" if result[0] else "deactivated"
        flash(f'Item {status}! ✅', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True, 'active': result[0]})
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/bulk', methods=['POST'])
@login_required
def admin_bulk_upload():
    csv_file = request.files.get('csv_file')
    csv_text = request.form.get('csv_text', '').strip()

    data = None
    if csv_file and csv_file.filename:
        stream = io.StringIO(csv_file.stream.read().decode("UTF8"), newline=None)
        data = csv.DictReader(stream)
    elif csv_text:
        stream = io.StringIO(csv_text)
        data = csv.DictReader(stream)
    else:
        flash('Please provide CSV file or paste CSV data!', 'error')
        return redirect(url_for('admin_dashboard'))

    conn = get_db()
    count = 0
    errors = 0
    try:
        cur = conn.cursor()
        for row in data:
            try:
                title = row.get('title', '').strip()
                url_val = row.get('url', '').strip()
                if not title or not url_val:
                    errors += 1
                    continue

                media_type = row.get('type', row.get('media_type', '')).strip()
                if not media_type:
                    media_type = detect_media_type(url_val)

                category = row.get('category', 'Uncategorized').strip() or 'Uncategorized'
                thumbnail = row.get('thumbnail', '').strip()
                duration = row.get('duration', '').strip()
                tags = row.get('tags', '').strip()

                if not thumbnail:
                    thumbnail = get_youtube_thumbnail(url_val)

                cur.execute("""
                    INSERT INTO media_items (title, url, media_type, category, thumbnail, duration, tags)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (title, url_val, media_type, category, thumbnail, duration, tags))

                cur.execute("""
                    INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING
                """, (category,))

                count += 1
            except Exception as e:
                errors += 1
                continue

        conn.commit()
        flash(f'Bulk upload: {count} items added, {errors} errors! 📦', 'success' if count > 0 else 'error')
    except Exception as e:
        conn.rollback()
        flash(f'Upload error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/category/add', methods=['POST'])
@login_required
def admin_add_category():
    name = request.form.get('name', '').strip()
    icon = request.form.get('icon', '📁').strip()
    color = request.form.get('color', '#6366f1').strip()

    if not name:
        flash('Category name required!', 'error')
        return redirect(url_for('admin_dashboard'))

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO categories (name, icon, color) VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET icon=%s, color=%s",
            (name, icon, color, icon, color)
        )
        conn.commit()
        flash(f'Category "{name}" added! ✅', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/delete-bulk', methods=['POST'])
@login_required
def admin_delete_bulk():
    ids = request.form.getlist('item_ids')
    if not ids:
        flash('No items selected!', 'error')
        return redirect(url_for('admin_dashboard'))

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM media_items WHERE id = ANY(%s)", (list(map(int, ids)),))
        conn.commit()
        flash(f'{len(ids)} items deleted! 🗑️', 'info')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'error')
    finally:
        cur.close()
        put_db(conn)
    return redirect(url_for('admin_dashboard'))


# ═══════════════════════════════════════════════════════════════
# HTML TEMPLATES
# ═══════════════════════════════════════════════════════════════

INDEX_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
    <title>Media Gallery</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎬</text></svg>">
    """ + BASE_CSS + """
</head>
<body>

<!-- TOP NAVBAR -->
<nav class="top-nav">
    <a href="/" class="nav-logo"><span>🎬</span> MediaHub</a>
    <div class="nav-actions">
        <a href="/admin" class="nav-btn">⚡ Admin</a>
    </div>
</nav>

<!-- SEARCH -->
<div class="search-container" style="margin-top:64px;">
    <div class="search-wrapper">
        <span class="search-icon">🔍</span>
        <form action="/" method="GET" id="searchForm">
            <input type="text" name="q" class="search-input"
                   placeholder="Search millions of media..."
                   value="{{ search }}" autocomplete="off" id="searchInput">
            {% if media_type %}<input type="hidden" name="type" value="{{ media_type }}">{% endif %}
            {% if category %}<input type="hidden" name="category" value="{{ category }}">{% endif %}
            {% if sort != 'newest' %}<input type="hidden" name="sort" value="{{ sort }}">{% endif %}
        </form>
        <button class="search-clear" id="searchClear" onclick="clearSearch()">✕</button>
    </div>
</div>

<!-- STATS BAR -->
<div class="stats-bar" style="margin-top:8px;">
    <div class="stat-pill">
        <span class="stat-number">{{ "{:,}".format(stats.total_active|int) }}</span>
        <span class="stat-label">Total</span>
    </div>
    <div class="stat-pill">
        <span class="stat-number">🎬 {{ "{:,}".format(stats.videos|int) }}</span>
        <span class="stat-label">Videos</span>
    </div>
    <div class="stat-pill">
        <span class="stat-number">🎵 {{ "{:,}".format(stats.audios|int) }}</span>
        <span class="stat-label">Audio</span>
    </div>
    <div class="stat-pill">
        <span class="stat-number">📷 {{ "{:,}".format(stats.images|int) }}</span>
        <span class="stat-label">Images</span>
    </div>
    <div class="stat-pill">
        <span class="stat-number">👁️ {{ "{:,}".format(stats.total_views|int) }}</span>
        <span class="stat-label">Views</span>
    </div>
</div>

<!-- FILTER CHIPS -->
<div class="filter-bar">
    <a href="/?{% if search %}q={{ search }}&{% endif %}sort={{ sort }}"
       class="filter-chip {% if not media_type and not category %}active{% endif %}">
        🌐 All
    </a>
    <a href="/?type=video{% if search %}&q={{ search }}{% endif %}&sort={{ sort }}"
       class="filter-chip {% if media_type == 'video' %}active{% endif %}">
        🎬 Videos
    </a>
    <a href="/?type=audio{% if search %}&q={{ search }}{% endif %}&sort={{ sort }}"
       class="filter-chip {% if media_type == 'audio' %}active{% endif %}">
        🎵 Audio
    </a>
    <a href="/?type=image{% if search %}&q={{ search }}{% endif %}&sort={{ sort }}"
       class="filter-chip {% if media_type == 'image' %}active{% endif %}">
        📷 Images
    </a>
    {% for cat in categories %}
    {% if cat.count > 0 %}
    <a href="/?category={{ cat.name }}{% if search %}&q={{ search }}{% endif %}&sort={{ sort }}"
       class="filter-chip {% if category == cat.name %}active{% endif %}">
        {{ cat.icon }} {{ cat.name }} <span class="count">{{ cat.count }}</span>
    </a>
    {% endif %}
    {% endfor %}
</div>

<!-- SORT -->
<div style="padding: 0 20px 10px; display: flex; align-items: center; justify-content: space-between;">
    <span style="color: var(--text-muted); font-size: 0.85rem;">
        {% if search %}Results for "<strong style="color:var(--accent-light)">{{ search }}</strong>" •{% endif %}
        {{ "{:,}".format(total) }} items
    </span>
    <select onchange="window.location.href=this.value" style="
        background: var(--bg-card); color: var(--text-primary);
        border: 1px solid var(--border); padding: 6px 12px;
        border-radius: var(--radius-full); font-size: 0.8rem; cursor: pointer;
    ">
        <option value="/?sort=newest{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}"
                {% if sort == 'newest' %}selected{% endif %}>🕐 Newest</option>
        <option value="/?sort=oldest{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}"
                {% if sort == 'oldest' %}selected{% endif %}>📅 Oldest</option>
        <option value="/?sort=popular{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}"
                {% if sort == 'popular' %}selected{% endif %}>🔥 Popular</option>
        <option value="/?sort=title{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}"
                {% if sort == 'title' %}selected{% endif %}>🔤 A-Z</option>
    </select>
</div>

<!-- CONTENT -->
<div class="content-area">
    {% if items %}
    <div class="masonry-grid">
        {% for item in items %}
        <div class="media-card" onclick="openModal({{ item.id }}, '{{ item.media_type }}', '{{ item.url|e }}', '{{ item.title|e }}', '{{ item.category|e }}', {{ item.view_count }}, '{{ item.thumbnail|e if item.thumbnail else '' }}')">
            <div class="card-thumb">
                {% if item.thumbnail %}
                <img src="{{ item.thumbnail }}" alt="{{ item.title }}"
                     loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                     style="min-height:120px;">
                <div class="no-thumb {{ item.media_type }}-placeholder" style="display:none; position:absolute; inset:0;">
                    {% if item.media_type == 'video' %}🎬{% elif item.media_type == 'audio' %}🎵{% else %}📷{% endif %}
                </div>
                {% elif item.media_type == 'image' %}
                <img src="{{ item.url }}" alt="{{ item.title }}"
                     loading="lazy" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                     style="min-height:120px;">
                <div class="no-thumb image-placeholder" style="display:none; position:absolute; inset:0;">📷</div>
                {% else %}
                <div class="no-thumb {{ item.media_type }}-placeholder">
                    {% if item.media_type == 'video' %}🎬{% elif item.media_type == 'audio' %}🎵{% else %}📷{% endif %}
                    {% if item.media_type == 'audio' %}
                    <div class="audio-wave" style="position:absolute; bottom:0; left:0; right:0;">
                        <div class="bar"></div><div class="bar"></div><div class="bar"></div>
                        <div class="bar"></div><div class="bar"></div><div class="bar"></div>
                        <div class="bar"></div>
                    </div>
                    {% endif %}
                </div>
                {% endif %}

                <span class="type-badge type-{{ item.media_type }}">
                    {{ item.media_type }}
                </span>
                {% if item.duration %}
                <span class="duration-badge">{{ item.duration }}</span>
                {% endif %}

                {% if item.media_type == 'video' %}
                <div class="play-overlay">
                    <div class="play-btn">
                        <svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    </div>
                </div>
                {% endif %}
            </div>
            <div class="card-body">
                <div class="card-title">{{ item.title }}</div>
                <div class="card-meta">
                    <span class="card-category">{{ item.category }}</span>
                    <span class="card-views">👁️ {{ "{:,}".format(item.view_count|int) }}</span>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div class="empty-state">
        <div class="icon">🔍</div>
        <h3>No media found</h3>
        <p>{% if search %}Try a different search term{% else %}Add some content via the admin panel{% endif %}</p>
    </div>
    {% endif %}

    <!-- PAGINATION -->
    {% if total_pages > 1 %}
    <div class="pagination">
        <a href="/?page={{ page-1 }}{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}&sort={{ sort }}"
           class="page-btn {% if page <= 1 %}disabled{% endif %}">‹</a>

        {% set start_page = [1, page - 2]|max %}
        {% set end_page = [total_pages, page + 2]|min %}

        {% if start_page > 1 %}
        <a href="/?page=1{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}&sort={{ sort }}" class="page-btn">1</a>
        {% if start_page > 2 %}<span class="page-btn disabled">…</span>{% endif %}
        {% endif %}

        {% for p in range(start_page, end_page + 1) %}
        <a href="/?page={{ p }}{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}&sort={{ sort }}"
           class="page-btn {% if p == page %}active{% endif %}">{{ p }}</a>
        {% endfor %}

        {% if end_page < total_pages %}
        {% if end_page < total_pages - 1 %}<span class="page-btn disabled">…</span>{% endif %}
        <a href="/?page={{ total_pages }}{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}&sort={{ sort }}" class="page-btn">{{ total_pages }}</a>
        {% endif %}

        <a href="/?page={{ page+1 }}{% if search %}&q={{ search }}{% endif %}{% if media_type %}&type={{ media_type }}{% endif %}{% if category %}&category={{ category }}{% endif %}&sort={{ sort }}"
           class="page-btn {% if page >= total_pages %}disabled{% endif %}">›</a>
    </div>
    {% endif %}
</div>

<!-- MODAL -->
<div class="modal-overlay" id="mediaModal">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <div class="modal-content">
        <div class="modal-player" id="modalPlayer"></div>
        <div class="modal-info">
            <div class="modal-title" id="modalTitle"></div>
            <div class="modal-meta" id="modalMeta"></div>
        </div>
    </div>
</div>

<!-- BOTTOM NAV (Mobile) -->
<nav class="bottom-nav">
    <a href="/" class="bnav-item active">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        Home
    </a>
    <a href="/?sort=popular" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        Trending
    </a>
    <a href="javascript:void(0)" onclick="document.getElementById('searchInput').focus(); window.scrollTo({top:0,behavior:'smooth'})" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        Search
    </a>
    <a href="/?type=video" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
        Videos
    </a>
    <a href="/admin" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        Admin
    </a>
</nav>

<script>
// Search
const searchInput = document.getElementById('searchInput');
const searchClear = document.getElementById('searchClear');
if(searchInput.value) searchClear.style.display = 'block';
searchInput.addEventListener('input', function() {
    searchClear.style.display = this.value ? 'block' : 'none';
});
searchInput.addEventListener('keydown', function(e) {
    if(e.key === 'Enter') document.getElementById('searchForm').submit();
});
function clearSearch() {
    searchInput.value = '';
    searchClear.style.display = 'none';
    window.location.href = '/';
}

// Modal
function openModal(id, type, url, title, category, views, thumbnail) {
    const modal = document.getElementById('mediaModal');
    const player = document.getElementById('modalPlayer');
    const modalTitle = document.getElementById('modalTitle');
    const modalMeta = document.getElementById('modalMeta');

    // Track view
    fetch('/api/view/' + id, { method: 'POST' });

    modalTitle.textContent = title;
    modalMeta.innerHTML = '<span style="color:var(--accent-light)">' + category + '</span> • 👁️ ' + views.toLocaleString() + ' views';

    if(type === 'video') {
        // Check if embeddable
        let embedUrl = url;
        if(url.includes('youtube.com/watch') || url.includes('youtu.be')) {
            let vid = '';
            if(url.includes('v=')) vid = url.split('v=')[1].split('&')[0];
            else if(url.includes('youtu.be/')) vid = url.split('youtu.be/')[1].split('?')[0];
            embedUrl = 'https://www.youtube.com/embed/' + vid + '?autoplay=1';
            player.innerHTML = '<iframe src="' + embedUrl + '" allowfullscreen allow="autoplay; encrypted-media"></iframe>';
        } else if(url.includes('vimeo.com/')) {
            let vid = url.split('vimeo.com/')[1].split('?')[0];
            embedUrl = 'https://player.vimeo.com/video/' + vid + '?autoplay=1';
            player.innerHTML = '<iframe src="' + embedUrl + '" allowfullscreen allow="autoplay"></iframe>';
        } else if(url.includes('dailymotion.com/video/')) {
            let vid = url.split('/video/')[1].split('?')[0];
            embedUrl = 'https://www.dailymotion.com/embed/video/' + vid + '?autoplay=1';
            player.innerHTML = '<iframe src="' + embedUrl + '" allowfullscreen allow="autoplay"></iframe>';
        } else {
            player.innerHTML = '<video controls autoplay style="width:100%;height:100%;background:#000"><source src="' + url + '"><p>Cannot play. <a href="' + url + '" target="_blank" style="color:var(--accent)">Open directly</a></p></video>';
        }
    } else if(type === 'audio') {
        player.style.aspectRatio = 'auto';
        player.innerHTML = '<div style="padding:40px; text-align:center; background: linear-gradient(135deg, #064e3b, #065f46);"><div style="font-size:4rem; margin-bottom:20px;">🎵</div><div class="audio-wave" style="justify-content:center; height:80px; margin-bottom:20px;"><div class="bar" style="width:6px;height:30%"></div><div class="bar" style="width:6px;height:60%"></div><div class="bar" style="width:6px;height:40%"></div><div class="bar" style="width:6px;height:80%"></div><div class="bar" style="width:6px;height:50%"></div><div class="bar" style="width:6px;height:70%"></div><div class="bar" style="width:6px;height:35%"></div><div class="bar" style="width:6px;height:90%"></div><div class="bar" style="width:6px;height:45%"></div></div><audio controls autoplay style="width:100%; max-width:500px;"><source src="' + url + '"></audio></div>';
    } else if(type === 'image') {
        player.innerHTML = '<img src="' + url + '" alt="' + title + '" style="width:100%; height:auto; max-height:80vh; object-fit:contain;">';
        player.style.aspectRatio = 'auto';
    }

    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    const modal = document.getElementById('mediaModal');
    const player = document.getElementById('modalPlayer');
    modal.classList.remove('active');
    player.innerHTML = '';
    player.style.aspectRatio = '16/9';
    document.body.style.overflow = '';
}

// Close on escape or backdrop
document.addEventListener('keydown', (e) => { if(e.key === 'Escape') closeModal(); });
document.getElementById('mediaModal').addEventListener('click', function(e) {
    if(e.target === this) closeModal();
});

// Lazy load images with IntersectionObserver
if('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if(entry.isIntersecting) {
                const img = entry.target;
                if(img.dataset.src) {
                    img.src = img.dataset.src;
                    img.removeAttribute('data-src');
                }
                observer.unobserve(img);
            }
        });
    }, { rootMargin: '200px' });
    document.querySelectorAll('img[loading="lazy"]').forEach(img => observer.observe(img));
}
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
# LOGIN TEMPLATE
# ═══════════════════════════════════════════════════════════════

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login • MediaHub</title>
    """ + BASE_CSS + """
</head>
<body>
<div class="login-container">
    <div class="login-card">
        <h1>🔐 Admin</h1>
        <p class="subtitle">Sign in to manage your media gallery</p>

        {% if error %}
        <div class="login-error">{{ error }}</div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label class="form-label">Username</label>
                <input type="text" name="username" class="form-input"
                       placeholder="Enter username" required autofocus>
            </div>
            <div class="form-group">
                <label class="form-label">Password</label>
                <input type="password" name="password" class="form-input"
                       placeholder="Enter password" required>
            </div>
            <button type="submit" class="btn btn-primary btn-block" style="margin-top:8px;">
                🚀 Sign In
            </button>
        </form>
        <p style="text-align:center; margin-top:20px;">
            <a href="/" style="color:var(--accent-light); text-decoration:none; font-size:0.85rem;">← Back to Gallery</a>
        </p>
    </div>
</div>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
# ADMIN DASHBOARD TEMPLATE
# ═══════════════════════════════════════════════════════════════

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard • MediaHub</title>
    """ + BASE_CSS + """
</head>
<body>

<nav class="top-nav">
    <a href="/" class="nav-logo"><span>🎬</span> MediaHub</a>
    <div class="nav-actions">
        <a href="/" class="nav-btn">🌐 View Site</a>
        <a href="/admin/logout" class="nav-btn" style="background:var(--danger); border-color:var(--danger);">🚪 Logout</a>
    </div>
</nav>

<!-- Toast Messages -->
<div class="toast-container" id="toastContainer">
    {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
    {% for cat, msg in messages %}
    <div class="toast {{ cat }}">
        {% if cat == 'success' %}✅{% elif cat == 'error' %}❌{% else %}ℹ️{% endif %}
        {{ msg }}
    </div>
    {% endfor %}
    {% endif %}
    {% endwith %}
</div>

<div class="admin-container">
    <div class="admin-header">
        <h1 class="admin-title">⚡ Admin Dashboard</h1>
    </div>

    <!-- STATS -->
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(99,102,241,0.15);">📊</div>
            <div>
                <div class="stat-number">{{ "{:,}".format(stats.total|int) }}</div>
                <div class="stat-label">Total Items</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(34,197,94,0.15);">✅</div>
            <div>
                <div class="stat-number" style="color:var(--success)">{{ "{:,}".format(stats.active|int) }}</div>
                <div class="stat-label">Active</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(239,68,68,0.15);">🎬</div>
            <div>
                <div class="stat-number" style="color:#ef4444">{{ stats.videos }}</div>
                <div class="stat-label">Videos</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(34,197,94,0.15);">🎵</div>
            <div>
                <div class="stat-number" style="color:#22c55e">{{ stats.audios }}</div>
                <div class="stat-label">Audio</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(59,130,246,0.15);">📷</div>
            <div>
                <div class="stat-number" style="color:#3b82f6">{{ stats.images }}</div>
                <div class="stat-label">Images</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(245,158,11,0.15);">👁️</div>
            <div>
                <div class="stat-number" style="color:var(--warning)">{{ "{:,}".format(stats.total_views|int) }}</div>
                <div class="stat-label">Total Views</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(168,85,247,0.15);">🕐</div>
            <div>
                <div class="stat-number" style="color:#a855f7">{{ stats.today }}</div>
                <div class="stat-label">Added Today</div>
            </div>
        </div>
        <div class="stat-card">
            <div class="stat-icon" style="background:rgba(236,72,153,0.15);">⚡</div>
            <div>
                <div class="stat-number" style="color:#ec4899">{{ stats.inactive }}</div>
                <div class="stat-label">Inactive</div>
            </div>
        </div>
    </div>

    <!-- ADD SINGLE ITEM -->
    <div class="admin-card" id="addSection">
        <h3>➕ Add New Media</h3>
        <form method="POST" action="/admin/add">
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap:14px;">
                <div class="form-group">
                    <label class="form-label">Title *</label>
                    <input type="text" name="title" class="form-input" placeholder="Enter title" required>
                </div>
                <div class="form-group">
                    <label class="form-label">URL *</label>
                    <input type="url" name="url" class="form-input" placeholder="https://..." required
                           id="addUrl" oninput="autoDetectType()">
                </div>
                <div class="form-group">
                    <label class="form-label">Type (auto-detected)</label>
                    <select name="media_type" class="form-select" id="addType">
                        <option value="">Auto-Detect</option>
                        <option value="video">🎬 Video</option>
                        <option value="audio">🎵 Audio</option>
                        <option value="image">📷 Image</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Category</label>
                    <select name="category" class="form-select">
                        {% for cat in categories %}
                        <option value="{{ cat.name }}">{{ cat.icon }} {{ cat.name }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Thumbnail URL</label>
                    <input type="url" name="thumbnail" class="form-input" placeholder="Auto-generated for YouTube">
                </div>
                <div class="form-group">
                    <label class="form-label">Duration</label>
                    <input type="text" name="duration" class="form-input" placeholder="e.g. 12:30">
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Tags (comma separated)</label>
                <input type="text" name="tags" class="form-input" placeholder="action, thriller, 2024">
            </div>
            <div class="form-group">
                <label class="form-label">Description</label>
                <textarea name="description" class="form-textarea" placeholder="Optional description..."></textarea>
            </div>
            <button type="submit" class="btn btn-primary">🚀 Add Item</button>
        </form>
    </div>

    <!-- BULK UPLOAD -->
    <div class="admin-card" id="bulkSection">
        <h3>📦 Bulk Upload (CSV)</h3>
        <form method="POST" action="/admin/bulk" enctype="multipart/form-data">
            <div class="drag-area" id="dragArea" onclick="document.getElementById('csvFile').click()">
                <div class="icon">📄</div>
                <p><strong>Click or drag CSV file here</strong></p>
                <p style="font-size:0.8rem; margin-top:4px;">Max millions of rows supported</p>
            </div>
            <input type="file" name="csv_file" id="csvFile" accept=".csv"
                   style="display:none" onchange="handleFile(this)">
            <div id="fileName" style="margin-top:8px; color:var(--accent-light); font-size:0.85rem;"></div>

            <div class="form-group" style="margin-top:16px;">
                <label class="form-label">Or paste CSV data directly:</label>
                <textarea name="csv_text" class="form-textarea" rows="6"
                    placeholder="title,url,type,category,thumbnail,duration,tags
My Video,https://youtube.com/watch?v=xxx,video,Movies,,12:30,action
My Song,https://example.com/song.mp3,audio,Music,,3:45,pop"></textarea>
            </div>

            <div class="upload-info">
                <strong>CSV Format:</strong> <code>title,url,type,category,thumbnail,duration,tags</code><br>
                • <code>type</code> = video / audio / image (optional, auto-detected)<br>
                • <code>thumbnail</code> & <code>duration</code> are optional<br>
                • YouTube thumbnails are auto-generated
            </div>

            <button type="submit" class="btn btn-success" style="margin-top:14px;">📦 Upload Bulk</button>
        </form>
    </div>

    <!-- ADD CATEGORY -->
    <div class="admin-card">
        <h3>📁 Add Category</h3>
        <form method="POST" action="/admin/category/add"
              style="display:flex; gap:10px; flex-wrap:wrap; align-items:end;">
            <div class="form-group" style="flex:1; min-width:150px;">
                <label class="form-label">Name</label>
                <input type="text" name="name" class="form-input" placeholder="Category name" required>
            </div>
            <div class="form-group" style="width:80px;">
                <label class="form-label">Icon</label>
                <input type="text" name="icon" class="form-input" value="📁" style="text-align:center;">
            </div>
            <div class="form-group" style="width:80px;">
                <label class="form-label">Color</label>
                <input type="color" name="color" value="#6366f1"
                       style="width:100%; height:44px; border:none; background:none; cursor:pointer;">
            </div>
            <button type="submit" class="btn btn-primary" style="height:44px;">Add</button>
        </form>
    </div>

    <!-- MEDIA TABLE -->
    <div class="admin-card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
            <h3 style="margin:0;">📋 All Media ({{ "{:,}".format(total) }})</h3>
            <form method="GET" action="/admin" style="display:flex; gap:8px;">
                <input type="text" name="q" class="form-input" placeholder="Search..."
                       value="{{ search }}" style="width:200px; padding:8px 14px;">
                <button type="submit" class="btn btn-secondary btn-sm">🔍</button>
            </form>
        </div>

        <form method="POST" action="/admin/delete-bulk" id="bulkDeleteForm">
            <div class="table-container">
                <table class="admin-table">
                    <thead>
                        <tr>
                            <th><input type="checkbox" id="selectAll" onchange="toggleSelectAll()"></th>
                            <th>Title</th>
                            <th>Type</th>
                            <th>Category</th>
                            <th>Views</th>
                            <th>Active</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for item in items %}
                        <tr>
                            <td>
                                <input type="checkbox" name="item_ids" value="{{ item.id }}" class="item-check">
                            </td>
                            <td class="title-cell" title="{{ item.title }}">
                                <div style="display:flex; align-items:center; gap:8px;">
                                    {% if item.media_type == 'video' %}🎬
                                    {% elif item.media_type == 'audio' %}🎵
                                    {% else %}📷{% endif %}
                                    <a href="{{ item.url }}" target="_blank"
                                       style="color:var(--text-primary); text-decoration:none;"
                                       title="{{ item.url }}">{{ item.title[:50] }}{% if item.title|length > 50 %}...{% endif %}</a>
                                </div>
                            </td>
                            <td>
                                <span class="type-badge type-{{ item.media_type }}"
                                      style="position:static;">{{ item.media_type }}</span>
                            </td>
                            <td style="color:var(--text-muted);">{{ item.category }}</td>
                            <td style="color:var(--text-muted);">{{ "{:,}".format(item.view_count|int) }}</td>
                            <td>
                                <label class="toggle-switch" title="Toggle active">
                                    <input type="checkbox" {% if item.is_active %}checked{% endif %}
                                           onchange="toggleItem({{ item.id }})">
                                    <span class="toggle-slider"></span>
                                </label>
                            </td>
                            <td>
                                <div style="display:flex; gap:6px;">
                                    <a href="/admin/edit/{{ item.id }}" class="btn btn-secondary btn-sm">✏️</a>
                                    <button type="button" class="btn btn-danger btn-sm"
                                            onclick="confirmDelete({{ item.id }})">🗑️</button>
                                </div>
                            </td>
                        </tr>
                        {% endfor %}
                        {% if not items %}
                        <tr><td colspan="7" class="text-center" style="padding:40px; color:var(--text-muted);">
                            No items found
                        </td></tr>
                        {% endif %}
                    </tbody>
                </table>
            </div>

            {% if items %}
            <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap;">
                <button type="submit" class="btn btn-danger btn-sm"
                        onclick="return confirm('Delete selected items?')">
                    🗑️ Delete Selected
                </button>
            </div>
            {% endif %}
        </form>

        <!-- Pagination -->
        {% if total_pages > 1 %}
        <div class="pagination">
            {% for p in range(1, total_pages + 1) %}
            <a href="/admin?page={{ p }}{% if search %}&q={{ search }}{% endif %}"
               class="page-btn {% if p == page %}active{% endif %}">{{ p }}</a>
            {% endfor %}
        </div>
        {% endif %}
    </div>
</div>

<!-- Bottom Nav Mobile -->
<nav class="bottom-nav">
    <a href="/" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        Site
    </a>
    <a href="#addSection" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
        Add
    </a>
    <a href="#bulkSection" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
        Bulk
    </a>
    <a href="/admin/logout" class="bnav-item" style="color:var(--danger)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        Logout
    </a>
</nav>

<script>
// Auto-detect media type from URL
function autoDetectType() {
    const url = document.getElementById('addUrl').value.toLowerCase();
    const sel = document.getElementById('addType');
    const videoExt = ['.mp4','.webm','.mov','.avi','.mkv','.m3u8'];
    const audioExt = ['.mp3','.wav','.ogg','.aac','.flac','.m4a'];
    const imageExt = ['.jpg','.jpeg','.png','.gif','.webp','.svg'];
    const videoDomains = ['youtube.com','youtu.be','vimeo.com','dailymotion.com'];

    for(const ext of videoExt) { if(url.endsWith(ext)) { sel.value='video'; return; } }
    for(const ext of audioExt) { if(url.endsWith(ext)) { sel.value='audio'; return; } }
    for(const ext of imageExt) { if(url.endsWith(ext)) { sel.value='image'; return; } }
    for(const d of videoDomains) { if(url.includes(d)) { sel.value='video'; return; } }
}

// Toggle item active/inactive
function toggleItem(id) {
    fetch('/admin/toggle/' + id, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(r => r.json()).then(data => {
        showToast(data.active ? 'Item activated ✅' : 'Item deactivated', data.active ? 'success' : 'info');
    });
}

// Delete single item
function confirmDelete(id) {
    if(confirm('Are you sure you want to delete this item?')) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = '/admin/delete/' + id;
        document.body.appendChild(form);
        form.submit();
    }
}

// Select all checkboxes
function toggleSelectAll() {
    const checked = document.getElementById('selectAll').checked;
    document.querySelectorAll('.item-check').forEach(cb => cb.checked = checked);
}

// Drag & Drop
const dragArea = document.getElementById('dragArea');
['dragenter','dragover'].forEach(e => {
    dragArea.addEventListener(e, (ev) => { ev.preventDefault(); dragArea.classList.add('dragover'); });
});
['dragleave','drop'].forEach(e => {
    dragArea.addEventListener(e, (ev) => { ev.preventDefault(); dragArea.classList.remove('dragover'); });
});
dragArea.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if(file) {
        document.getElementById('csvFile').files = e.dataTransfer.files;
        handleFile(document.getElementById('csvFile'));
    }
});
function handleFile(input) {
    if(input.files[0]) {
        document.getElementById('fileName').textContent = '📄 ' + input.files[0].name + ' (' + (input.files[0].size/1024).toFixed(1) + ' KB)';
    }
}

// Toast
function showToast(msg, type='info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.innerHTML = (type==='success'?'✅':type==='error'?'❌':'ℹ️') + ' ' + msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// Auto-hide toasts
setTimeout(() => {
    document.querySelectorAll('.toast').forEach(t => t.remove());
}, 3000);
</script>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
# EDIT TEMPLATE
# ═══════════════════════════════════════════════════════════════

EDIT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Edit: {{ item.title }} • MediaHub</title>
    """ + BASE_CSS + """
</head>
<body>

<nav class="top-nav">
    <a href="/" class="nav-logo"><span>🎬</span> MediaHub</a>
    <div class="nav-actions">
        <a href="/admin" class="nav-btn">← Back</a>
    </div>
</nav>

<div class="admin-container">
    <div class="admin-header">
        <h1 class="admin-title">✏️ Edit Media</h1>
    </div>

    <div class="admin-card">
        <form method="POST">
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap:14px;">
                <div class="form-group">
                    <label class="form-label">Title *</label>
                    <input type="text" name="title" class="form-input"
                           value="{{ item.title }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label">URL *</label>
                    <input type="url" name="url" class="form-input"
                           value="{{ item.url }}" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Type</label>
                    <select name="media_type" class="form-select">
                        <option value="video" {% if item.media_type=='video' %}selected{% endif %}>🎬 Video</option>
                        <option value="audio" {% if item.media_type=='audio' %}selected{% endif %}>🎵 Audio</option>
                        <option value="image" {% if item.media_type=='image' %}selected{% endif %}>📷 Image</option>
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Category</label>
                    <select name="category" class="form-select">
                        {% for cat in categories %}
                        <option value="{{ cat.name }}"
                                {% if item.category == cat.name %}selected{% endif %}>
                            {{ cat.icon }} {{ cat.name }}
                        </option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">Thumbnail URL</label>
                    <input type="url" name="thumbnail" class="form-input"
                           value="{{ item.thumbnail or '' }}">
                </div>
                <div class="form-group">
                    <label class="form-label">Duration</label>
                    <input type="text" name="duration" class="form-input"
                           value="{{ item.duration or '' }}">
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Tags</label>
                <input type="text" name="tags" class="form-input"
                       value="{{ item.tags or '' }}">
            </div>
            <div class="form-group">
                <label class="form-label">Description</label>
                <textarea name="description" class="form-textarea">{{ item.description or '' }}</textarea>
            </div>
            <div class="form-group" style="display:flex; align-items:center; gap:12px;">
                <label class="toggle-switch">
                    <input type="checkbox" name="is_active"
                           {% if item.is_active %}checked{% endif %}>
                    <span class="toggle-slider"></span>
                </label>
                <span class="form-label" style="margin:0;">Active</span>
            </div>

            {% if item.thumbnail %}
            <div style="margin:16px 0;">
                <img src="{{ item.thumbnail }}" style="max-width:300px; border-radius:var(--radius-sm);"
                     onerror="this.style.display='none'">
            </div>
            {% endif %}

            <div style="display:flex; gap:10px; margin-top:20px;">
                <button type="submit" class="btn btn-primary">💾 Save Changes</button>
                <a href="/admin" class="btn btn-secondary">Cancel</a>
            </div>
        </form>
    </div>

    <div class="admin-card" style="border-color:rgba(239,68,68,0.3);">
        <h3 style="color:var(--danger);">⚠️ Danger Zone</h3>
        <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:14px;">
            Permanently delete this item. This action cannot be undone.
        </p>
        <form method="POST" action="/admin/delete/{{ item.id }}"
              onsubmit="return confirm('Are you sure? This cannot be undone!')">
            <button type="submit" class="btn btn-danger">🗑️ Delete Permanently</button>
        </form>
    </div>
</div>

<nav class="bottom-nav">
    <a href="/admin" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
        Back
    </a>
    <a href="/" class="bnav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        Site
    </a>
</nav>
</body>
</html>
"""

# ═══════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════

with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"⚠️ DB init deferred: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
