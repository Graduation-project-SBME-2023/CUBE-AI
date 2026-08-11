import os
import httpx
import sys

def remove_spleen_segmentation(volume_uid: str):
    # Determine the API URL from environment variables, defaulting to localhost:8001
    app_url = os.environ.get("APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")).rstrip("/")
    
    with httpx.Client(base_url=app_url) as client:
        print(f"Fetching segmentations for volume {volume_uid}...")
        try:
            resp = client.get(f"/segmentations/by-volume/{volume_uid}")
        except httpx.RemoteProtocolError as e:
            print(f"Server disconnected for {volume_uid}: {e}")
            return
            
        if resp.status_code != 200:
            print(f"Failed to fetch segmentations for {volume_uid}: {resp.status_code} {resp.text}")
            return
        
        segmentations = resp.json()
        deleted = False
        
        for seg in segmentations:
            target_label = seg.get("target_label") or ""
            name = seg.get("name") or ""
            
            if target_label.lower() == "spleen" or name.lower() == "spleen":
                label_uid = seg.get("label_series_instance_uid")
                
                if not label_uid:
                    print(f"Cannot find label_series_instance_uid in segmentation: {seg}")
                    continue
                    
                print(f"Deleting spleen segmentation {label_uid} for volume {volume_uid}...")
                
                try:
                    del_resp = client.delete(f"/segmentations/{label_uid}")
                except httpx.RemoteProtocolError as e:
                    print(f"Server disconnected while deleting {label_uid}: {e}")
                    continue
                     
                if del_resp.status_code in (200, 204):
                    print(f"Successfully deleted {label_uid}")
                else:
                    print(f"Failed to delete {label_uid}: {del_resp.status_code} {del_resp.text}")
                deleted = True
        
        if not deleted:
            print(f"No spleen segmentations found for volume {volume_uid}")

if __name__ == "__main__":
    volume_uids = [
        "1.3.6.1.4.1.14519.5.2.1.6919.4624.319956314021047038498210610134"
    ]
    for uid in volume_uids:
        remove_spleen_segmentation(uid)
