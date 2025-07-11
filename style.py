from tkinter import ttk

class Style(ttk.Style):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # --- COLOR PALETTE ---
        self.COLOR_DARK_GRAY = "#282c34"
        self.COLOR_MEDIUM_GRAY = "#3e4451"
        self.COLOR_LIGHT_GRAY = "#abb2bf"
        self.COLOR_ACCENT = "#61afef"
        self.COLOR_SUCCESS = "#98c379"
        self.COLOR_ERROR = "#e06c75"
        self.COLOR_WHITE = "#ffffff"

        self.master.tk_setPalette(
            background=self.COLOR_DARK_GRAY,
            foreground=self.COLOR_LIGHT_GRAY,
            activeBackground=self.COLOR_MEDIUM_GRAY,
            activeForeground=self.COLOR_WHITE,
            highlightColor=self.COLOR_ACCENT,
            highlightBackground=self.COLOR_DARK_GRAY
        )

        self.configure("TFrame", background=self.COLOR_DARK_GRAY)
        self.configure("TLabel", background=self.COLOR_DARK_GRAY, foreground=self.COLOR_LIGHT_GRAY, padding=5, font=("Segoe UI", 10))
        self.configure("TCheckbutton", background=self.COLOR_DARK_GRAY, foreground=self.COLOR_LIGHT_GRAY, font=("Segoe UI", 10), indicatorrelief="flat")
        self.map("TCheckbutton",
                 foreground=[('active', self.COLOR_WHITE)],
                 background=[('active', self.COLOR_MEDIUM_GRAY)])
                 
        self.configure("TButton", background=self.COLOR_ACCENT, foreground=self.COLOR_DARK_GRAY, padding=6, font=("Segoe UI", 10, "bold"), borderwidth=0)
        self.map("TButton",
                 background=[('active', self.COLOR_WHITE), ('disabled', self.COLOR_MEDIUM_GRAY)],
                 foreground=[('active', self.COLOR_DARK_GRAY), ('disabled', self.COLOR_LIGHT_GRAY)])

        self.configure("TLabelframe", background=self.COLOR_DARK_GRAY, bordercolor=self.COLOR_MEDIUM_GRAY, relief="solid", borderwidth=1)
        self.configure("TLabelframe.Label", background=self.COLOR_DARK_GRAY, foreground=self.COLOR_ACCENT, font=("Segoe UI", 11, "bold"))
        
        self.configure("Vertical.TScrollbar", background=self.COLOR_DARK_GRAY, troughcolor=self.COLOR_MEDIUM_GRAY, bordercolor=self.COLOR_DARK_GRAY, arrowcolor=self.COLOR_LIGHT_GRAY)
        
        self.layout("Vertical.TScrollbar",
            [('Vertical.Scrollbar.trough', {'children':
                [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
            'sticky': 'ns'})])
            
        self.configure("TProgressbar", thickness=10, background=self.COLOR_ACCENT, troughcolor=self.COLOR_MEDIUM_GRAY)

        # Custom Listbox styling (as it's not a ttk widget)
        self.listbox_bg = self.COLOR_MEDIUM_GRAY
        self.listbox_fg = self.COLOR_WHITE
        self.listbox_select_bg = self.COLOR_ACCENT
        self.listbox_select_fg = self.COLOR_DARK_GRAY
        
        self.success_color = self.COLOR_SUCCESS
        self.error_color = self.COLOR_ERROR 