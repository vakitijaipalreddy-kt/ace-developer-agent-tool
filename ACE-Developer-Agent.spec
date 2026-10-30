# PyInstaller spec for the ACE Developer Agent.
#
# Produces a single windowed .exe. `console=False` is what keeps a terminal
# from appearing behind the UI - the same reason the app uses pythonw when
# run from source.
#
# Build with:  python -m PyInstaller ACE-Developer-Agent.spec --noconfirm

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("README.md", "."),
    ],
    hiddenimports=[
        # tkinter pieces PyInstaller does not always follow
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
        # our own modules, imported lazily in places
        "agent.config",
        "agent.doctor",
        "agent.engine",
        "agent.gui",
        "agent.jira",
        "agent.permissions",
        "agent.pipeline",
        "agent.proc",
        "agent.settings_dialog",
        "agent.terminal",
        "agent.theme",
        "agent.vcs",
        # networking + corporate TLS
        "requests",
        "truststore",
        # the embedded terminal's ConPTY backend
        "winpty",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # keep the executable small - none of these are used
        "numpy", "pandas", "matplotlib", "scipy", "PIL",
        "pytest", "PyInstaller", "setuptools", "pip",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ACE Developer Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # no terminal window behind the UI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)
