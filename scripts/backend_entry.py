"""PyInstaller 独立入口。"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from backend.desktop import main
    main()
