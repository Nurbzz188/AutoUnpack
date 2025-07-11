<div align="center">
  
# AutoUnpack for qBittorrent

**Tired of manually extracting your qBittorrent downloads? Let AutoUnpack handle it for you!**

</div>

AutoUnpack is a user-friendly desktop application that automates the entire process of unpacking downloaded archives. It seamlessly integrates with qBittorrent's WebUI to monitor for completed torrents, unpack them using 7-Zip, and keep you informed with a clean, modern interface.

---

### ➤ Key Features

- **🚀 Fully Automated**: Set it up once and let it run. AutoUnpack watches your folders and processes downloads as soon as they're complete.
- **🔌 qBittorrent Integration**: Connects directly to the qBittorrent WebUI to check torrent progress and pause downloads before extraction, preventing file corruption.
- **📦 Universal Unpacking**: Powered by 7-Zip, it handles all common archive types, including `.rar`, `.zip`, `.7z`, and even complex multi-part archives.
- **🖥️ Modern GUI**: A sleek, dark-themed interface built with `tkinter` lets you control everything with ease.
- ** MANUAL SCAN**: Process any torrents that were downloaded while the app was closed with a single click.
- **📖 Extraction History**: A color-coded log shows your successful (✅ Green) and failed (❌ Red) extractions. You can even open the destination folder directly from the history.
- **⚙️ Flexible Options**:
    - Automatically delete archives after a successful extraction to keep your folders clean.
    - Choose between creating a tidy subfolder for each download or extracting files directly into the torrent's root folder.
- **💾 Persistent & Real-Time Settings**: Your configuration is saved in real-time and automatically loaded on launch. No need to re-enter settings.

---

### ➤ Requirements

| Component         | Requirement                               |
| ----------------- | ----------------------------------------- |
| **Operating System** | Windows                                   |
| **Python**        | Version 3.x                               |
| **qBittorrent**   | WebUI must be enabled in the settings.    |
| **7-Zip**         | Must be installed on your system.         |
| **Libraries**     | `qbittorrent-api`, `watchdog`             |

---

### ➤ Installation & Setup

Getting started is easy. Just follow these steps:

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/your-username/autounpack.git
    cd autounpack
    ```
    *(Don't forget to replace `your-username` with your actual GitHub username!)*

2.  **Install Dependencies**:
    The project comes with a `requirements.txt` file to make this step simple.
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the Application**:
    ```bash
    python autounpack.py
    ```

4.  **First-Time Configuration**:
    When you first launch the app, you'll need to configure your settings:
    -   **qBittorrent Settings**: Enter your WebUI host, port, username, and password.
    -   **Folder Paths**:
        -   **Monitor Folder**: Select the main folder where qBittorrent saves your downloads.
        -   **7-Zip Path**: Point the app to your `7z.exe` file (e.g., `C:\Program Files\7-Zip\7z.exe`).
    -   **Tweak Your Options**: Enable or disable features like deleting archives or creating subfolders to match your workflow.

    Your settings are saved automatically, so you'll only need to do this once!

---

### ➤ How to Use

-   **Start/Stop**: Use the **"Save & Start"** button to begin monitoring. The status label will confirm that it's active.
-   **Manual Scan**: Click **"Manual Scan"** to find and unpack any completed torrents that were downloaded before you started the monitor.
-   **View Logs**: The **"View Logs"** button opens a detailed activity log, perfect for troubleshooting or seeing what the app is doing in real-time.
-   **Manage History**: The main window shows a list of all unpacking jobs. Select any item and click **"Open Folder"** to jump right to the extracted files.

---

### ➤ Contributing

Found a bug or have an idea for a new feature? Feel free to open an issue or submit a pull request! 