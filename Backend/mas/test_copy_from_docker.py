"""
Test script that uses your exact inference code and copies file from Docker
immediately after inference, before parsing response.
"""
import asyncio
import httpx
import subprocess
import os
import json
import re
import time
import hashlib
from pathlib import Path
from requests_toolbelt import multipart as decoder


# ==================== CONFIGURATION ====================
MONAI_LABEL_URL = "http://localhost:8000"
CONTAINER_NAME = "monailabel-backend"
TEST_SERIES_UID = "1.2.826.0.1.3680043.8.498.12073285427930663774611907954116957406"
MODEL_NAME = "segmentation_spleen"
OUTPUT_DIR = Path(__file__).parent / "test_inference_output"

# ==================== HELPER FUNCTIONS ====================

def print_section(title):
    """Print formatted section header"""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


def get_filename_from_response(response):
    """
    Extract temp filename from multipart response.
    Returns filename or None.
    """
    try:
        content_type = response.headers.get('content-type', '')
        
        if 'multipart' not in content_type.lower():
            return None
        
        multipart = decoder.MultipartDecoder(response.content, content_type)
        
        if len(multipart.parts) < 2:
            return None
        
        # Extract filename from Content-Disposition header
        disposition = multipart.parts[1].headers.get(b'Content-Disposition', b'').decode('utf-8')
        match = re.search(r'filename="([^"]+)"', disposition)
        
        if match:
            return match.group(1)
        
        return None
        
    except Exception as e:
        print(f"⚠ Error extracting filename: {e}")
        return None


def docker_cp_file(container_name: str, source_path: str, dest_path: str):
    """
    Copy file from Docker container using docker cp.
    Returns dict with success status and metadata.
    """
    try:
        cmd = ["docker", "cp", f"{container_name}:{source_path}", dest_path]
        print(f"📋 Command: {' '.join(cmd)}")
        
        start_time = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        duration = time.time() - start_time
        
        if result.returncode == 0:
            if os.path.exists(dest_path):
                file_size = os.path.getsize(dest_path)
                
                with open(dest_path, 'rb') as f:
                    md5 = hashlib.md5(f.read()).hexdigest()
                
                print(f"✓ SUCCESS - File copied in {duration:.2f}s")
                print(f"  Destination: {dest_path}")
                print(f"  File size: {file_size:,} bytes ({file_size/1024:.2f} KB)")
                print(f"  MD5 hash: {md5}")
                
                return {
                    "success": True,
                    "path": dest_path,
                    "file_size": file_size,
                    "md5": md5,
                    "duration": duration
                }
            else:
                print(f"✗ Command succeeded but file not found at destination")
                return {"success": False, "error": "File not found after copy"}
        else:
            print(f"✗ FAILED")
            print(f"  Return code: {result.returncode}")
            print(f"  Error: {result.stderr}")
            return {
                "success": False,
                "error": result.stderr,
                "returncode": result.returncode
            }
            
    except subprocess.TimeoutExpired:
        print(f"✗ TIMEOUT - Command took longer than 30 seconds")
        return {"success": False, "error": "Timeout"}
    except Exception as e:
        print(f"✗ EXCEPTION: {e}")
        return {"success": False, "error": str(e)}


# ==================== MAIN TEST FUNCTION ====================

async def test_inference_with_docker_copy():
    """
    Test using your exact inference code with Docker copy before parsing response.
    """
    print_section("Inference with Docker Copy Test")
    
    # Prepare variables (your exact code)
    clean_model = MODEL_NAME
    series_instance_uid = TEST_SERIES_UID
    params = None
    
    # Normalize label info
    normalized_label_info = {"spleen": 1}
    
    # Build request
    infer_url = f"{MONAI_LABEL_URL}/infer/{clean_model}"
    query_params = {"image": series_instance_uid}
    
    inference_params = params or {}
    inference_params["label_info"] = normalized_label_info
    
    files = {"params": (None, json.dumps(inference_params), "application/json")}
    
    # ========== YOUR EXACT INFERENCE CODE ==========
    print(f"\n🚀 Starting inference request...")
    print(f"  URL: {infer_url}")
    print(f"  Series: {series_instance_uid}")
    
    inference_start = time.time()
    
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            response = await client.post(infer_url, params=query_params, files=files)
            inference_duration = time.time() - inference_start
            print(f"✓ Inference completed in {inference_duration:.2f}s with status: {response.status_code}")
        except Exception as e:
            print(f"✗ Inference request failed: {str(e)}")
            return {"error": True, "stage": "inference_request", "message": str(e)}
    
    result = {
        "endpoint": infer_url,
        "query_params": query_params,
        "sent_params": inference_params,
        "status_code": response.status_code,
    }
    
    if response.status_code != 200:
        result["error"] = True
        result["server_response"] = response.text[:500]
        print(f"✗ Inference failed: {response.status_code}")
        return result
    
    # ========== IMMEDIATELY EXTRACT FILENAME FROM RESPONSE ==========
    print_section("Extracting Filename and Copying from Docker")
    
    print(f"📄 Extracting temp filename from response...")
    new_filename = get_filename_from_response(response)
    
    if not new_filename:
        print(f"✗ Could not extract filename from response")
        return {"error": True, "message": "No filename found in response"}
    
    print(f"✓ Found temp filename: {new_filename}")
    
    # Copy from Docker
    source_path = f"/tmp/{new_filename}"
    dest_filename = f"seg_{series_instance_uid}_{clean_model}.nii.gz"
    OUTPUT_DIR.mkdir(exist_ok=True)
    dest_path = OUTPUT_DIR / dest_filename
    
    print(f"\n📋 Copying from Docker container...")
    print(f"  Source: {CONTAINER_NAME}:{source_path}")
    print(f"  Dest: {dest_path}")
    
    copy_result = docker_cp_file(CONTAINER_NAME, source_path, str(dest_path))
    
    if copy_result["success"]:
        print(f"\n✅ Docker copy successful!")
        result["copy_method"] = "docker_cp"
        result["file_path"] = copy_result["path"]
        result["file_size"] = copy_result["file_size"]
        result["md5"] = copy_result["md5"]
        result["copy_duration"] = copy_result["duration"]
        result["error"] = False
        
        # Now parse response for metadata
        print(f"\n📊 Parsing response metadata...")
        try:
            content_type = response.headers.get('content-type', '')
            multipart = decoder.MultipartDecoder(response.content, content_type)
            
            json_bytes = multipart.parts[0].content
            metadata = json.loads(json_bytes.decode('utf-8').strip())
            
            result["metadata"] = metadata
            print(f"✓ Metadata parsed successfully")
            print(f"  Labels: {metadata.get('label_names', {})}")
            print(f"  Latencies: {metadata.get('latencies', {}).get('total', 'N/A')}s")
            
        except Exception as e:
            print(f"⚠ Could not parse metadata: {e}")
            result["metadata"] = None
        
        return result
    
    else:
        # Fallback to multipart bytes
        print(f"\n⚠ Docker copy failed, falling back to multipart data...")
        print(f"  Error: {copy_result.get('error')}")
        
        try:
            content_type = response.headers.get('content-type', '')
            multipart = decoder.MultipartDecoder(response.content, content_type)
            
            # Get metadata
            metadata = json.loads(multipart.parts[0].content.decode('utf-8').strip())
            
            # Get segmentation bytes
            seg_bytes = multipart.parts[1].content
            
            # Save file
            fallback_path = OUTPUT_DIR / f"fallback_{dest_filename}"
            with open(fallback_path, 'wb') as f:
                f.write(seg_bytes)
            
            file_size = os.path.getsize(fallback_path)
            
            print(f"✓ File saved via multipart fallback")
            print(f"  Path: {fallback_path}")
            print(f"  Size: {file_size:,} bytes")
            
            result["copy_method"] = "multipart_fallback"
            result["file_path"] = str(fallback_path)
            result["file_size"] = file_size
            result["metadata"] = metadata
            result["error"] = False
            
            return result
            
        except Exception as e:
            print(f"✗ Multipart fallback also failed: {e}")
            result["error"] = True
            result["message"] = f"Both Docker copy and multipart failed: {str(e)}"
            return result


# ==================== MAIN ====================

async def main():
    """Run the test"""
    print("\n" + "🧪 Inference + Docker Copy Test ".center(70, "="))
    print(f"MONAI URL: {MONAI_LABEL_URL}")
    print(f"Container: {CONTAINER_NAME}")
    print(f"Series: {TEST_SERIES_UID}")
    print(f"Model: {MODEL_NAME}")
    print(f"Output: {OUTPUT_DIR}")
    
    # Run test
    result = await test_inference_with_docker_copy()
    
    # Summary
    print_section("Test Summary")
    
    if result.get("error"):
        print(f"✗ TEST FAILED")
        print(f"  Error: {result.get('message', 'Unknown error')}")
    else:
        print(f"✓ TEST PASSED")
        print(f"  Method: {result.get('copy_method')}")
        print(f"  File: {result.get('file_path')}")
        print(f"  Size: {result.get('file_size', 0):,} bytes")
        if result.get('md5'):
            print(f"  MD5: {result.get('md5')}")
        
        print(f"\n📊 Performance:")
        print(f"  Inference: {result.get('inference_duration', 'N/A')}")
        if result.get('copy_duration'):
            print(f"  Copy: {result['copy_duration']:.3f}s")
    
    print("\n" + "="*70)
    
    # Print recommendation
    if result.get("copy_method") == "docker_cp":
        print("\n✅ RECOMMENDATION: Use Docker copy method in production")
        print("   This method is more reliable than multipart parsing")
    elif result.get("copy_method") == "multipart_fallback":
        print("\n⚠ RECOMMENDATION: Docker copy failed, but multipart works")
        print("   Consider fixing Docker access or use multipart as fallback")
    
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted by user")
    except Exception as e:
        print(f"\n\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()