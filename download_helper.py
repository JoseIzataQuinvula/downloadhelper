import customtkinter as ctk
from customtkinter import CTkImage
from PIL import Image, ImageTk 
from io import BytesIO
import urllib.request
import re
import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled
import threading
import os
import subprocess
import logging
import queue
from typing import Dict, Optional, Any
import platform
import random

# ==============================
# CONFIGURAÇÕES E UTILS
# ==============================
ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

MAX_TITLE_LENGTH = 70
MAX_URL_DISPLAY_LENGTH = 70 
DOWNLOAD_PATH = os.path.join(os.path.expanduser('~'), 'Downloads', 'DownloadHelper')
THUMB_SIZE = (160, 120)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG_DIR = os.path.join(SCRIPT_DIR, 'ffmpeg')

# FFmpeg multiplataforma
SYSTEM = platform.system()
FFMPEG_EXE = {
    'Windows': os.path.join(FFMPEG_DIR, 'ffmpeg.exe'),
    'Darwin': os.path.join(FFMPEG_DIR, 'ffmpeg'),
    'Linux': os.path.join(FFMPEG_DIR, 'ffmpeg')
}.get(SYSTEM, os.path.join(FFMPEG_DIR, 'ffmpeg'))
FFPROBE_EXE = FFMPEG_EXE.replace('ffmpeg', 'ffprobe')

# User-Agents reais
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version=18.1 Safari/605.1.15',
]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """Limpa o nome para uso em arquivos, mantendo um comprimento razoável."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name[:MAX_TITLE_LENGTH].strip().rstrip('_').strip() or "video"

def _format_duration(sec: Optional[int]) -> str:
    """Formata a duração em segundos para HH:MM:SS ou MM:SS."""
    if not sec: return "—"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def _format_url_display(url: str, max_len: int = MAX_URL_DISPLAY_LENGTH) -> str:
    """Abrevia URLs longas com reticências (...) no meio para preservar a legibilidade."""
    if len(url) <= max_len:
        return url
    
    half_len = (max_len - 3) // 2 
    start = url[:half_len]
    end = url[len(url) - half_len:]
    
    return f"{start}...{end}"

# ==============================
# CLASSE PRINCIPAL
# ==============================
class DownloadHelper(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Download Helper 2025")
        self.geometry("960x660")
        self.minsize(800, 500)

        # Estado
        self.url_cache: Dict[str, Dict] = {}
        self.media_boxes: Dict[str, Dict] = {}
        self.active_searches: Dict[str, threading.Event] = {}
        self.active_downloads: Dict[str, Dict[str, Any]] = {} 
        self.downloaded_videos: Dict[str, int] = {}
        self.total_videos: Dict[str, int] = {}
        self.task_queue = queue.Queue() 

        # Configurações de Paths
        self.ffmpeg_path = FFMPEG_EXE
        self.ffprobe_path = FFPROBE_EXE
        if not os.path.exists(self.ffmpeg_path):
            logger.warning("FFmpeg não encontrado. Alguns formatos/junções podem falhar.")

        # UI
        self._setup_window_icon()
        self._setup_ui()
        self._start_task_processor()
        self._setup_keyboard()

    def _setup_window_icon(self):
        """Tenta carregar e aplicar o ícone da janela."""
        icon_path = os.path.join(SCRIPT_DIR, 'assets', 'icon.png')
        if os.path.exists(icon_path):
            try:
                pil_img = Image.open(icon_path).resize((32, 32), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(pil_img) 
                self.iconphoto(True, photo)
                self._window_icon = photo 
            except Exception as e:
                logger.error(f"Erro ao carregar ícone: {e}")

    def _setup_ui(self):
        # Header (Inclui botão Limpar Histórico)
        header = ctk.CTkFrame(self, height=40, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        # Botão "Limpar Histórico"
        self.clear_button = ctk.CTkButton(
            header,
            text="Limpar Histórico",
            width=130,
            height=30,
            command=self._clear_history,
            fg_color="#ef4444",
            hover_color="#dc2626"
        )
        self.clear_button.pack(side="right", padx=10, pady=5)

        # Search Frame
        search_frame = ctk.CTkFrame(self, height=60)
        search_frame.pack(fill="x", padx=20, pady=(0, 12))
        search_frame.pack_propagate(False) 

        self.url_entry = ctk.CTkEntry(
            search_frame,
            placeholder_text="Cole a URL do YouTube, playlist ou vídeo...",
            height=42,
            font=ctk.CTkFont(size=14)
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=9)
        self.url_entry.focus()

        self.search_button = ctk.CTkButton(
            search_frame,
            text="Pesquisar",
            width=130,
            height=42,
            command=self.handle_search
        )
        self.search_button.pack(side="right", pady=9)

        # Scrollable Content
        self.scrollable = ctk.CTkScrollableFrame(self, corner_radius=12)
        self.scrollable.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def _setup_keyboard(self):
        self.url_entry.bind("<Return>", lambda e: self.handle_search())
        self.bind("<Escape>", lambda e: self.url_entry.delete(0, "end"))
        self.bind("<Control-l>", lambda e: self.url_entry.focus())

    def _start_task_processor(self):
        """Processa tarefas da fila na thread principal da UI (Thread-safe)."""
        def loop():
            while True:
                try:
                    task = self.task_queue.get_nowait()
                    task()
                    self.task_queue.task_done()
                except queue.Empty:
                    break
                except Exception as e:
                    logger.error(f"Erro na tarefa da UI: {e}")
            self.after(50, loop)
        self.after(50, loop)

    # ==============================
    # BUSCA E INFO
    # ==============================
    
    def _update_status_from_disk(self, url: str) -> bool:
        """Atualiza o status de download de um item no cache baseado no disco local."""
        if url in self.url_cache:
            info = self.url_cache[url]
            new_status = self._is_file_downloaded(info)
            if info.get("download_status") != new_status:
                self.url_cache[url]["download_status"] = new_status
            return True
        return False
        
    def handle_search(self):
        """Inicia a busca de informações da URL em uma thread separada."""
        url = self.url_entry.get().strip()
        if not url:
            self._show_message("Cole uma URL válida.")
            return
        
        if not url.startswith("http"):
             url = "https://" + url
        if not re.match(r'^https?://', url, re.I):
            self._show_message("URL inválida.")
            return

        is_cached = self._update_status_from_disk(url)
        
        if is_cached and self.url_cache[url].get("type") != "error":
            data = self.url_cache[url]
            self.create_media_box(url, data)
            if url in self.media_boxes:
                 self.media_boxes[url]["box"].lift() 
            self.task_queue.put(lambda: self.url_entry.delete(0, "end"))
            self._show_message(f"Status local de {data['title'][:40]}... atualizado.")
            return

        if url in self.active_searches:
            self._show_message("Já pesquisando esta URL...")
            return

        self.task_queue.put(lambda: self.url_entry.delete(0, "end"))
        self.active_searches[url] = threading.Event()
        loading = self._create_loading_box(url)
        threading.Thread(target=self._fetch_info, args=(url, loading), daemon=True).start()

    def _fetch_info(self, url: str, loading_box):
        """Busca informações da URL (thread worker)."""
        try:
            info = self._get_youtube_info(url)
            
            if not info:
                raise ValueError("URL não suportada ou formato desconhecido.") 
            
            self.url_cache[url] = info
            self.task_queue.put(lambda: self._destroy(loading_box))
            self.task_queue.put(lambda: self.create_media_box(url, info)) 
            
        except Exception as e:
            logger.error(f"Erro ao buscar: {e}")
            error_msg = str(e).split('\n')[0] 
            self.url_cache[url] = {"type": "error", "title": error_msg}
            self.task_queue.put(lambda: self._destroy(loading_box))
            self.task_queue.put(lambda: self._show_message(f"Erro ao obter informações: {error_msg[:100]}"))
        finally:
            self.task_queue.put(lambda: self.active_searches.pop(url, None))

    def _get_target_path(self, info: Dict) -> str:
        """Determina o caminho do diretório de destino (sempre uma subpasta)."""
        title = info.get("title", "Item Desconhecido")
        sanitized_title = sanitize_filename(title)
        return os.path.join(DOWNLOAD_PATH, sanitized_title)

    def _count_finished_files(self, target_dir: str) -> int:
        """Conta arquivos não parciais na pasta de destino."""
        if not os.path.isdir(target_dir):
            return 0
        try:
            # Conta arquivos que NÃO terminam com .part e NÃO são diretórios.
            count = len([
                f for f in os.listdir(target_dir) 
                if not f.endswith('.part') and not os.path.isdir(os.path.join(target_dir, f))
            ])
            return count
        except Exception as e:
            logger.warning(f"Erro ao contar arquivos em {target_dir}: {e}")
            return 0

    def _is_file_downloaded(self, info: Dict) -> Optional[str]:
        """Verifica se o arquivo/pasta já foi baixado (baixado) ou tem .part (pausado) no diretório de destino."""
        if not info or info.get("type") == "error":
            return None
            
        target_dir = self._get_target_path(info)
        
        if not os.path.isdir(target_dir):
            return None

        files = os.listdir(target_dir)
        
        # 🎯 USANDO O NOVO MÉTODO: Contagem precisa de arquivos finalizados
        file_count = self._count_finished_files(target_dir) 
        
        part_count = len([f for f in files if f.endswith('.part')])
        
        if file_count > 0 and part_count == 0:
            return "baixado"
        if part_count > 0:
            return "pausado" 
        
        return None

    def _get_youtube_info(self, url: str) -> Optional[Dict]:
        """Tenta obter informações do YouTube/yt-dlp e adiciona status local."""
        try:
            opts = {
                "quiet": True, "skip_download": True, "no_warnings": True,
                "http_headers": {"User-Agent": random.choice(USER_AGENTS)},
                "ffmpeg_location": self.ffmpeg_path,
                "ffprobe_location": self.ffprobe_path,
            }
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info: return None

            if "entries" in info:
                entries = [e for e in info.get("entries", []) if e and e.get("id")]
                if not entries: return None
                base_info = {
                    "type": "playlist",
                    "title": info.get("title", "Playlist Desconhecida"),
                    "num_videos": len(entries),
                    "size": "Calculando...", 
                    "thumbnail": self._fetch_thumbnail(entries[0].get("thumbnail") or info.get("thumbnail")), 
                    "url": url
                }
            else:
                duration = info.get("duration")
                size = info.get("filesize") or info.get("filesize_approx")
                size_str = f"{size/1024/1024:.1f} MB" if size else "—"
                base_info = {
                    "type": "video",
                    "title": info.get("title", "Vídeo Desconhecido"),
                    "duration": _format_duration(duration),
                    "size": size_str,
                    "thumbnail": self._fetch_thumbnail(info.get("thumbnail")),
                    "url": url
                }

            base_info["download_status"] = self._is_file_downloaded(base_info)
            return base_info

        except Exception as e:
            logger.error(f"Erro yt-dlp: {e}")
            return None

    def _fetch_thumbnail(self, url: Optional[str]) -> Optional[CTkImage]:
        """Baixa e retorna a imagem em formato CTkImage."""
        if not url: return None
        try:
            req = requests.get(url, headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=8)
            req.raise_for_status() 
            img = Image.open(BytesIO(req.content)).resize(THUMB_SIZE, Image.Resampling.LANCZOS)
            return CTkImage(light_image=img, dark_image=img, size=THUMB_SIZE)
        except Exception as e:
            logger.warning(f"Erro ao baixar thumbnail de {url}: {e}")
            return None

    # ==============================
    # UI: CAIXAS
    # ==============================
    def _create_loading_box(self, url: str) -> ctk.CTkFrame:
        """Cria e exibe a caixa de carregamento."""
        box = ctk.CTkFrame(self.scrollable, corner_radius=12, height=60)
        box.pack(fill="x", pady=6, padx=5)
        box.pack_propagate(False)
        prog = ctk.CTkProgressBar(box, mode="indeterminate")
        prog.pack(side="left", fill="x", expand=True, padx=15, pady=15)
        prog.start()
        ctk.CTkButton(box, text="Cancelar", width=80, 
                      command=lambda: self._cancel_search(box, url)).pack(side="right", padx=15)
        return box

    def _cancel_search(self, box, url):
        """Cancela a busca e destrói a caixa."""
        self._destroy(box)
        self.active_searches.pop(url, None)
        self._show_message("Pesquisa cancelada.")

    def create_media_box(self, url: str, data: Dict):
        """Cria e exibe a caixa de mídia (vídeo ou playlist)."""
        if url in self.media_boxes:
            self.media_boxes[url]["box"].destroy()

        # Garante que o status no cache está atualizado antes de renderizar
        self._update_status_from_disk(url)
        data = self.url_cache.get(url, data) 

        box = ctk.CTkFrame(self.scrollable, corner_radius=14)
        box.pack(fill="x", pady=8, padx=5)
        self.media_boxes[url] = {"box": box, "data": data}

        # Header (URL formatada com reticências)
        header = ctk.CTkFrame(box, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 6))
        
        formatted_url = _format_url_display(url)
        ctk.CTkLabel(header, text=formatted_url, anchor="w", font=ctk.CTkFont(size=11, slant="italic")).pack(side="left")

        # Verifica status para controle de 'X'
        status = data.get("download_status")
        is_active = url in self.active_downloads
        is_downloaded = status == "baixado"
        
        # O 'X' é exibido apenas se o download não estiver ativo E o item não estiver BAIXADO.
        if not is_active and not is_downloaded: 
            close_button = ctk.CTkButton(header, text="✕", width=30, height=30, 
                                        fg_color="transparent", text_color="gray50", hover_color="#f87171",
                                        command=lambda: self._close_box(box, url))
            close_button.pack(side="right")
        
        # Body (Thumbnail e Info)
        body = ctk.CTkFrame(box, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=6)

        # Thumbnail
        thumb_label = ctk.CTkLabel(body, text="")
        if data.get("thumbnail"):
            thumb_label.configure(image=data["thumbnail"])
        else:
            thumb_label.configure(text="SEM THUMBNAIL", width=THUMB_SIZE[0], height=THUMB_SIZE[1], corner_radius=8, fg_color="gray50")
        thumb_label.pack(side="left", padx=(0, 16))

        # Info e Botão
        info = ctk.CTkFrame(body, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True)

        title = data["title"][:80] + "..." if len(data["title"]) > 80 else data["title"]
        ctk.CTkLabel(info, text=title, font=ctk.CTkFont(size=16, weight="bold"), anchor="w", wraplength=500).pack(fill="x", pady=(0, 4))

        if data["type"] == "video":
            ctk.CTkLabel(info, text=f"Duração: {data.get('duration', '—')}", anchor="w").pack(fill="x")
            ctk.CTkLabel(info, text=f"Tamanho: {data.get('size', '—')}", anchor="w").pack(fill="x")
        else:
            ctk.CTkLabel(info, text=f"Tipo: Playlist/Lista ({data.get('num_videos', '—')} vídeos)", anchor="w").pack(fill="x")
        
        # Botão Ação
        action_button_frame = ctk.CTkFrame(info, fg_color="transparent")
        action_button_frame.pack(pady=10, anchor="e")

        if status == "baixado":
            ctk.CTkButton(action_button_frame, text="Abrir Pasta / Re-Verificar", width=180, height=38, fg_color="#64748b", hover_color="#475569", 
                          command=lambda: self._open_folder_from_info(data)).pack(side="right")
        elif status == "pausado":
            
            # Botão 2: Cancelar Transferência (Limpa os arquivos .part)
            ctk.CTkButton(action_button_frame, text="Cancelar Transferência", width=160, height=38, 
                          fg_color="#dc2626", hover_color="#ef4444", 
                          command=lambda: self._handle_paused_cancel(url, data)).pack(side="right", padx=(10, 0))
            
            # Botão 1: Continuar Download
            ctk.CTkButton(action_button_frame, text="Continuar Download", width=140, height=38,
                          command=lambda: self._start_download(url, data, box)).pack(side="right")
        else:
            ctk.CTkButton(action_button_frame, text="Baixar Agora", width=140, height=38,
                          command=lambda: self._start_download(url, data, box)).pack(side="right")

    def _open_folder_from_info(self, data: Dict):
        """Abre a pasta correta baseada no item de info (vídeo ou playlist)."""
        path = self._get_target_path(data)
        self._open_folder(path)

    def _clear_history(self):
        """Limpa todos os widgets dentro do frame scrollable, limpando o histórico da tela."""
        for widget in self.scrollable.winfo_children():
            self._destroy(widget)
        
        self.url_cache.clear()
        self.media_boxes.clear()
        self.active_downloads.clear()
        self.downloaded_videos.clear()
        self.total_videos.clear()
        self._show_message("Histórico da tela limpo.")

    # ==============================
    # UI: PROGRESSO E CONTROLE DE INTERRUPÇÃO
    # ==============================
    def _start_download(self, url, data, parent):
        """Prepara e inicia o download na thread worker com progresso duplo."""
        if url in self.active_downloads:
            self._show_message("Já baixando esta URL...")
            return

        for w in parent.winfo_children():
            w.destroy()

        # Cria a UI de Progresso
        prog_frame = ctk.CTkFrame(parent, fg_color="transparent")
        prog_frame.pack(fill="x", padx=16, pady=12)

        # 1. Barra de Progresso da Playlist (Geral)
        self.prog_status_geral = ctk.CTkLabel(prog_frame, text="", anchor="w", text_color="#2563eb")
        self.prog_bar_geral = ctk.CTkProgressBar(prog_frame, fg_color="#bfdbfe", progress_color="#2563eb")
        self.prog_bar_geral.set(0)

        # 2. Status do Item Atual
        self.prog_status_item = ctk.CTkLabel(prog_frame, text="Iniciando download...", anchor="w", wraplength=800)
        self.prog_status_item.pack(fill="x", pady=(8, 0))
        
        # 3. Barra de Progresso do Item Atual
        self.prog_bar_item = ctk.CTkProgressBar(prog_frame)
        self.prog_bar_item.pack(fill="x", pady=6)
        self.prog_bar_item.set(0)

        # Evento de pausa (Thread-safe)
        event = threading.Event()
        # Armazena o evento e a ação padrão
        self.active_downloads[url] = {"event": event, "interrupt_action": "pause"} 
        
        # Frame para botões de controle
        control_frame = ctk.CTkFrame(prog_frame, fg_color="transparent")
        control_frame.pack(pady=4, anchor="e")

        # Botão Cancelar Transferência (Limpa os arquivos .part)
        cancel_button = ctk.CTkButton(control_frame, text="Cancelar Transferência", width=160, fg_color="#dc2626", hover_color="#ef4444",
                                      command=lambda: self._handle_download_interrupt(url, event, "cancel")) 
        cancel_button.pack(side="right", padx=(10, 0))

        # Botão Pausar Download (Mantém os arquivos .part)
        pause_button = ctk.CTkButton(control_frame, text="Pausar Download", width=140, fg_color="#f59e0b", hover_color="#d97706",
                                     command=lambda: self._handle_download_interrupt(url, event, "pause")) 
        pause_button.pack(side="right")
        
        # Armazena os botões para desabilitá-los na interrupção
        self.active_downloads[url]["action_buttons"] = [pause_button, cancel_button]

        # Configurações iniciais para Playlist
        if data["type"] == "playlist":
            total = data.get("num_videos", 0)
            self.total_videos[url] = total
            # 🎯 INICIALIZA o contador com o que já existe no disco
            target_dir = self._get_target_path(data)
            self.downloaded_videos[url] = self._count_finished_files(target_dir) 
            
            done = self.downloaded_videos[url]
            self.prog_status_geral.configure(text=f"Playlist: {done}/{total} vídeos concluídos")
            self.prog_bar_geral.set(done / total)
            self.prog_status_geral.pack(fill="x")
            self.prog_bar_geral.pack(fill="x", pady=6)

        threading.Thread(target=self._download_worker,
                         args=(url, data, event, parent), daemon=True).start()
        
    def _handle_download_interrupt(self, url: str, event: threading.Event, action: str):
        """
        Interrompe o download e define a ação desejada (pause/cancel).
        """
        if url in self.active_downloads:
            # Associa a ação desejada ao estado
            self.active_downloads[url]["interrupt_action"] = action
            # Sinaliza a interrupção para a thread worker
            event.set()
        
        # Desabilitar botões imediatamente após o clique
        try:
            for btn in self.active_downloads[url]["action_buttons"]:
                btn.configure(state="disabled")
        except:
            pass

    def _download_worker(self, url, data, event, parent):
        """Worker de download (thread separada) - Lógica de hook ajustada para contagem de disco."""
        is_playlist = data["type"] == "playlist"
        status_item = self.prog_status_item
        bar_item = self.prog_bar_item
        status_geral = self.prog_status_geral if is_playlist else None
        bar_geral = self.prog_bar_geral if is_playlist else None
        
        try:
            target_folder = self._get_target_path(data)
            os.makedirs(target_folder, exist_ok=True)
            
            outtmpl = os.path.join(target_folder, '%(playlist_index)02d - %(title)s.%(ext)s') if is_playlist else os.path.join(target_folder, '%(title)s.%(ext)s') 

            def smart_hook(d):
                """Hook para atualizar o progresso nas DUAS barras E verificar o PAUSE/CANCEL."""
                if event.is_set():
                    raise DownloadCancelled 

                if d['status'] == 'downloading':
                    try:
                        p = d.get('_percent_str', '0%').strip()
                        s = d.get('_speed_str', '').strip() or "0B/s"
                        e = d.get('_eta_str', '').strip() or "--"
                        downloaded = d.get('downloaded_bytes', 0)
                        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                        
                        def update_ui_item():
                            if status_item.winfo_exists() and bar_item.winfo_exists():
                                status_item.configure(text=f"Baixando: {p} | {s} | ETA {e}")
                                bar_item.set(downloaded / total)
                        self.task_queue.put(update_ui_item)
                    except:
                        pass

                elif d['status'] == 'finished':
                    if is_playlist:
                        target_dir = self._get_target_path(data)
                        
                        # 🎯 CORREÇÃO CRÍTICA: Conta quantos arquivos finalizados existem na pasta.
                        done = self._count_finished_files(target_dir) 

                        self.downloaded_videos[url] = done # Atualiza o contador com o valor real do disco.
                        total_v = self.total_videos.get(url, 1)

                        def update_playlist_progress():
                            if status_geral and bar_geral and status_geral.winfo_exists():
                                status_geral.configure(text=f"Playlist: {done}/{total_v} vídeos concluídos")
                                bar_geral.set(done / total_v) 
                                
                            if status_item.winfo_exists():
                                if done < total_v:
                                    status_item.configure(text="Iniciando próximo vídeo...")
                                    bar_item.set(0)
                                    
                                if done >= total_v:
                                    self._download_done(parent, d['filename'], url)
                        self.task_queue.put(update_playlist_progress)
                    else:
                        self.task_queue.put(lambda: self._download_done(parent, d['filename'], url))

            # Opções do yt-dlp
            opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
                'outtmpl': outtmpl,
                'progress_hooks': [smart_hook],
                'ffmpeg_location': self.ffmpeg_path,
                'ffprobe_location': self.ffprobe_path,
                'merge_output_format': 'mp4',
                'concurrent_fragment_downloads': 5,
                'noplaylist': False if is_playlist else True,
                'ignoreerrors': True if is_playlist else False,
                'http_headers': {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Referer': 'https://www.youtube.com/',
                },
                'retries': 5, 'fragment_retries': 5, 'logtostderr': False,
                'no_warnings': True, 'cachedir': False,
            }
            
            with YoutubeDL(opts) as ydl:
                ydl.download([url])

        except DownloadCancelled:
            # FLUXO DE TRATAMENTO DE INTERRUPÇÃO
            interrupt_action = self.active_downloads.get(url, {}).get("interrupt_action", "pause")
            target_folder = self._get_target_path(data)
            
            if interrupt_action == "cancel":
                # Ação: CANCELAR (limpa arquivos)
                self.url_cache[url]["download_status"] = None # Reseta o status para "Baixar Agora"
                self.task_queue.put(lambda: self._download_cancelled(parent, url, target_folder) if parent.winfo_exists() else None)
            else: # "pause" (Padrão)
                # Ação: PAUSAR (mantém arquivos)
                self.url_cache[url]["download_status"] = "pausado"
                self.task_queue.put(lambda: self._download_paused(parent, url) if parent.winfo_exists() else None)

        except Exception as e:
            logger.error(f"Erro no download: {e}")
            msg = "YouTube Bloqueou (403) ou erro de rede/yt-dlp." if "403" in str(e) or "HTTP Error 404" in str(e) else str(e).split('\n')[0][:100]
            self.url_cache[url]["download_status"] = None 
            self.task_queue.put(lambda: self._download_error(parent, msg, url) if parent.winfo_exists() else None)
        finally:
            # Remove o estado ativo do download
            self.task_queue.put(lambda: self.active_downloads.pop(url, None))
            self.downloaded_videos.pop(url, None)
            self.total_videos.pop(url, None)
            
    # ==============================
    # FINALIZAÇÃO/MENSAGENS E LIMPEZA
    # ==============================
    
    def _clean_part_files(self, folder_path):
        """Limpa arquivos parciais (.part) e remove a pasta se ficar vazia (REUSÁVEL)."""
        try:
            for f in os.listdir(folder_path):
                full_path = os.path.join(folder_path, f)
                if f.endswith('.part') and os.path.exists(full_path):
                    os.remove(full_path)
            # Remove a pasta se estiver vazia
            if os.path.exists(folder_path) and not os.listdir(folder_path):
                 os.rmdir(folder_path)
        except Exception as e:
            logger.warning(f"Erro ao limpar arquivos parciais: {e}")

    def _handle_paused_cancel(self, url, data):
        """Lida com o cancelamento de um download que estava pausado."""
        target_folder = self._get_target_path(data)
        
        # 1. Limpa os arquivos parciais (.part)
        self._clean_part_files(target_folder)
        
        # 2. Reseta o status no cache
        self.url_cache[url]["download_status"] = None 
        
        # 3. Atualiza a UI para o estado "Baixar Agora"
        self._refresh_media_box_state(url)
        self._show_message("Download pausado e arquivos parciais removidos.")

    def _download_done(self, parent, path, url):
        """Atualiza a UI após download bem-sucedido."""
        self.url_cache[url]["download_status"] = "baixado"
        self.task_queue.put(lambda: self._refresh_media_box_state(url))
        self._show_message("Download concluído com sucesso.")

    def _download_paused(self, parent, url):
        """Atualiza a UI após pausa/cancelamento."""
        self.task_queue.put(lambda: self._refresh_media_box_state(url))
        self._show_message("Download pausado.")
        
    def _download_cancelled(self, parent, url, folder_path):
        """
        Atualiza a UI após o cancelamento do download e limpa os arquivos parciais.
        """
        # Limpa arquivos parciais
        self._clean_part_files(folder_path)

        # Recria a caixa de mídia para mostrar o botão "Baixar Agora"
        self.task_queue.put(lambda: self._refresh_media_box_state(url))
        self._show_message("Download cancelado. Arquivos parciais removidos.")

    def _download_error(self, parent, msg, url):
        """Atualiza a UI após erro."""
        self.task_queue.put(lambda: self._refresh_media_box_state(url))
        self._show_message(f"Erro no download: {msg}")

    def _refresh_media_box_state(self, url):
        """Atualiza o estado da caixa de mídia após uma pausa, conclusão ou erro."""
        if url in self.url_cache:
            data = self.url_cache[url]
            if url in self.media_boxes and self.media_boxes[url]["box"].winfo_exists():
                self.media_boxes[url]["box"].destroy()
            self.create_media_box(url, data)

    def _open_folder(self, path):
        """Abre o explorador de arquivos na pasta de destino (multi-plataforma)."""
        try:
            os.makedirs(path, exist_ok=True) 
            if os.name == 'nt':
                subprocess.run(['explorer', path])
            elif SYSTEM == 'Darwin':
                subprocess.run(['open', path])
            else:
                subprocess.run(['xdg-open', path])
        except Exception as e:
            self._show_message(f"Erro ao abrir pasta: {e}")

    def _show_message(self, text):
        """Exibe uma mensagem temporária na UI (Neutro)."""
        box = ctk.CTkFrame(self.scrollable, corner_radius=8, fg_color="gray90") 
        box.pack(fill="x", pady=4, padx=5)
        ctk.CTkLabel(box, text=text, anchor="w", padx=15, pady=5, text_color="gray20").pack(fill="x", expand=True)
        self.after(5000, lambda: self._destroy(box) if box.winfo_exists() else None)
        
    def _close_box(self, box, url):
        """Fecha a caixa de mídia e remove dados do cache/estado."""
        self._destroy(box)
        self.media_boxes.pop(url, None)
        self.url_cache.pop(url, None)
        # Cancela o download ativo se a box for fechada
        if url in self.active_downloads:
             self.active_downloads[url]["interrupt_action"] = "cancel" # Define como cancelamento para limpar
             self.active_downloads[url]["event"].set() 
             self.active_downloads.pop(url, None) 

    def _destroy(self, widget):
        """Destrói o widget com segurança."""
        try:
            if widget.winfo_exists():
                widget.destroy()
        except:
            pass

# ==============================
# EXECUÇÃO
# ==============================
if __name__ == "__main__":
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)
    app = DownloadHelper()
    app.mainloop()