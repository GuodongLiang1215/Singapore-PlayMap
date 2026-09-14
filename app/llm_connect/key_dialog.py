"""Local masked credential entry. No HTTP, clipboard polling, or secret logging."""
from __future__ import annotations
from pathlib import Path
from .config import clean_model, persist_settings
from .errors import LLMConnectionError


class KeyDialog:
    """Kept separately so save/cancel and actual Tk controls can be tested."""
    def __init__(self, window, project_root: Path, model: str):
        import tkinter as tk
        from tkinter import ttk
        self.window = window
        self.project_root = Path(project_root)
        self.model = clean_model(model)
        self.saved = False
        self.window.title('PlayMap - Gemini Key Setup (Key Fix 1)')
        self.window.geometry('690x340')
        self.window.minsize(580, 330)
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='只在本机保存 Gemini API Key', font=('', 13, 'bold')).pack(anchor='w')
        ttk.Label(panel, text='Model: ' + self.model).pack(anchor='w', pady=(8, 0))
        ttk.Label(panel, text='在下方密码框按 Ctrl+V 粘贴完整 Key；不要复制遮掩预览或项目名称。',
                  wraplength=640).pack(anchor='w', pady=(10, 8))
        self.entry = ttk.Entry(panel, show='*', width=70)
        self.entry.pack(fill='x')
        self.entry.focus_set()
        self.entry.bind('<Control-a>', self.select_all)
        self.entry.bind('<Control-A>', self.select_all)
        self.entry.bind('<Return>', lambda event: self.save())
        self.status = tk.StringVar(value='仅写入本机 .env；不联网、不验证额度、不显示密钥。')
        ttk.Label(panel, textvariable=self.status, wraplength=640).pack(anchor='w', pady=(12, 10))
        buttons = ttk.Frame(panel)
        buttons.pack(fill='x', pady=(5, 10))
        ttk.Button(buttons, text='保存到本机', command=self.save).pack(side='left')
        ttk.Button(buttons, text='取消', command=self.cancel).pack(side='left', padx=10)
        ttk.Label(panel, text='.env 是明文配置文件，请勿截图、上传或提交到 Git。',
                  wraplength=640).pack(anchor='w', pady=(7, 0))
        self.window.bind('<Escape>', lambda event: self.cancel())

    def select_all(self, event=None):
        self.entry.selection_range(0, 'end')
        self.entry.icursor('end')
        return 'break'

    def save(self):
        key = self.entry.get()
        try:
            persist_settings(self.project_root, key, self.model)
        except LLMConnectionError as error:
            self.status.set('[ERROR] ' + error.code + '  ' + error.message)
            self.entry.focus_set()
            self.entry.selection_range(0, 'end')
            return
        except Exception:
            self.status.set('[ERROR] 本机保存失败；未显示异常细节或密钥。请检查文件权限。')
            return
        finally:
            key = None
        self.saved = True
        self.entry.delete(0, 'end')
        self.window.destroy()

    def cancel(self):
        self.entry.delete(0, 'end')
        self.window.destroy()


def open_key_dialog(project_root: Path, model: str) -> bool:
    window = None
    try:
        import tkinter as tk
        window = tk.Tk()
        dialog = KeyDialog(window, project_root, model)
        window.mainloop()
        return dialog.saved
    except (KeyboardInterrupt, EOFError):
        raise
    except LLMConnectionError:
        raise
    except Exception:
        raise LLMConnectionError('GUI_UNAVAILABLE') from None
    finally:
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
