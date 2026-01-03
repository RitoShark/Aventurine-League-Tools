import bpy
import bpy.utils.previews
import urllib.request
import urllib.error
import json
import os
import shutil
import zipfile
import threading
import ssl

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

class LOL_OT_CheckForUpdates(bpy.types.Operator):
    bl_idname = "lol.check_updates"
    bl_label = "Check for Updates"
    bl_description = "Check GitHub for the latest version of Aventurine League Tools"
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        repo_owner = prefs.repo_owner
        repo_name = prefs.repo_name
        
        if not repo_owner or not repo_name:
            self.report({'ERROR'}, "Please set Repository Owner and Name in preferences")
            return {'CANCELLED'}
            
        print(f"[Aventurine] Checking for updates from {repo_owner}/{repo_name}...")
        
        thread = threading.Thread(target=self.check_update_thread, args=(context, repo_owner, repo_name))
        thread.start()
        
        return {'FINISHED'}
    
    def check_update_thread(self, context, owner, repo):
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            req = urllib.request.Request(url, headers={'User-Agent': 'Blender-Aventurine-Updater'})
            
            with urllib.request.urlopen(req, context=ssl_context) as response:
                data = json.loads(response.read().decode())
                
                tag_name = data.get('tag_name', '').strip()
                if tag_name.lower().startswith('v'):
                    version_str = tag_name[1:]
                else:
                    version_str = tag_name
                    
                try:
                    new_version = tuple(map(int, version_str.split('.')))
                except:
                    print(f"FAILED to parse version: {version_str}")
                    return

                from .. import bl_info 
                current_version = bl_info['version']
                
                print(f"[Aventurine] Current: {current_version}, Latest: {new_version}")

                if new_version > current_version:
                    prefs = bpy.context.preferences.addons[__package__].preferences
                    prefs.update_available = True
                    prefs.latest_version_str = tag_name
                    prefs.download_url = data.get('zipball_url', '') 
                    
                    assets = data.get('assets', [])
                    for asset in assets:
                         if asset['name'].endswith('.zip'):
                             prefs.download_url = asset['browser_download_url']
                             break
                    
                    print(f"[Aventurine] Update available! {prefs.download_url}")
                else:
                    prefs = bpy.context.preferences.addons[__package__].preferences
                    prefs.update_available = False
                    print(f"[Aventurine] Up to date.")

        except Exception as e:
            print(f"[Aventurine] Update check failed: {e}")

class LOL_OT_UpdateAddon(bpy.types.Operator):
    bl_idname = "lol.update_addon"
    bl_label = "Update Addon"
    bl_description = "Download and install the latest version"
    
    def execute(self, context):
        prefs = context.preferences.addons[__package__].preferences
        url = prefs.download_url
        
        if not url:
            self.report({'ERROR'}, "No download URL found")
            return {'CANCELLED'}
        
        thread = threading.Thread(target=self.install_update_thread, args=(context, url))
        thread.start()
        
        return {'FINISHED'}

    def install_update_thread(self, context, url):
        import tempfile
        
        try:
            print(f"[Aventurine] Downloading update from {url}...")
            
            req = urllib.request.Request(url, headers={'User-Agent': 'Blender-Aventurine-Updater'})
            with urllib.request.urlopen(req, context=ssl_context) as response:
                data = response.read()
                
            tmp_dir = tempfile.gettempdir()
            zip_path = os.path.join(tmp_dir, "aventurine_update.zip")
            
            with open(zip_path, 'wb') as f:
                f.write(data)
                
            print(f"[Aventurine] Downloaded to {zip_path}")
            
            def install_on_main():
                try:
                    bpy.ops.preferences.addon_install(overwrite=True, filepath=zip_path)
                    
                    prefs = bpy.context.preferences.addons[__package__].preferences
                    prefs.update_available = False
                    prefs.latest_version_str = "Updated! Please Restart Blender."
                    
                    bpy.ops.wm.save_userpref()
                    
                    print(f"[Aventurine] Update installed successfully.")
                except Exception as e:
                    print(f"[Aventurine] Install failed: {e}")
            
            bpy.app.timers.register(install_on_main, first_interval=0.1)

        except Exception as e:
            print(f"[Aventurine] Update download failed: {e}")
