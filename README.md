# ⚡ DownloadHelper: Gerenciador de Downloads YouTube

## 🎯 Resumo do Projeto

**DownloadHelper é uma ferramenta de código aberto para baixar e gerenciar vídeos/playlists do YouTube. Ele oferece gestão de download retomável (pausa/continuação) e um status de arquivo no disco totalmente preciso.**

---

## 🛠️ Estrutura e Distribuição

A aplicação Python (`download_helper.py`) e suas dependências (`requirements.txt`) exigem que os binários do FFmpeg e recursos visuais (`assets/icon.png`) estejam presentes na raiz.

A distribuição final é um executável único (`DownloadHelper.exe` ou similar), gerado via PyInstaller, que **empacota todos os arquivos necessários** (incluindo a pasta `ffmpeg`) para garantir que o usuário final só precise do arquivo principal da pasta `dist`.

| Caminho Essencial | Propósito |
| :--- | :--- |
| `download_helper.py` | Ponto de entrada e lógica principal. |
| `requirements.txt` | Lista de bibliotecas Python. |
| `ffmpeg/` | Binários obrigatórios (ffmpeg, ffprobe) para processamento de vídeo. |
| `assets/` | Recursos gráficos (ícone). |
