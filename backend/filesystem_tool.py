import os
import glob
import subprocess
from typing import List, Dict, Any, Optional

class FileSystemTool:
    @classmethod
    def search_files(cls, query: str, search_path: Optional[str] = None) -> Dict[str, Any]:
        if not search_path:
            # Default to current project or user home
            search_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
        matches = []
        base_depth = search_path.rstrip(os.sep).count(os.sep)
        try:
            for root, dirs, files in os.walk(search_path):
                cur_depth = root.rstrip(os.sep).count(os.sep) - base_depth
                if cur_depth > 3:
                    dirs[:] = []
                    continue

                # Skip heavy system/node_modules/git folders
                dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', '__pycache__', 'dist', 'build', 'AppData', '.gemini']]
                
                for file in files:
                    if query.lower() in file.lower():
                        full_path = os.path.join(root, file)
                        matches.append({
                            "fileName": file,
                            "path": full_path,
                            "sizeBytes": os.path.getsize(full_path) if os.path.exists(full_path) else 0
                        })
                        if len(matches) >= 20:
                            break
                if len(matches) >= 20:
                    break
                    
            return {
                "success": True,
                "query": query,
                "count": len(matches),
                "results": matches
            }
        except Exception as e:
            return {
                "success": False,
                "query": query,
                "error": str(e),
                "results": []
            }

    @classmethod
    def read_file(cls, file_path: str, max_chars: int = 4000) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            return {"success": False, "error": f"File '{file_path}' not found."}
            
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(max_chars)
                
            return {
                "success": True,
                "filePath": file_path,
                "content": content,
                "truncated": len(content) >= max_chars
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def write_file(cls, file_path: str, content: str) -> Dict[str, Any]:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
                
            return {
                "success": True,
                "filePath": file_path,
                "message": f"Successfully written to '{file_path}'"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def open_local_path(cls, target_path: str) -> Dict[str, Any]:
        if not os.path.exists(target_path):
            return {"success": False, "error": f"Path '{target_path}' does not exist."}
            
        try:
            os.startfile(target_path)
            return {
                "success": True,
                "path": target_path,
                "message": f"Opened '{target_path}' in OS viewer"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
