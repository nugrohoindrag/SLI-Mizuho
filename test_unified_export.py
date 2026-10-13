import requests
import sys
from datetime import datetime, timedelta

# Base URL - adjust if needed
BASE_URL = "http://localhost:8000"

def test_unified_export():
    """
    Test that all export types use the same endpoint with different type parameters.
    """
    print("Testing unified export functionality...")
    
    # Set up test parameters
    start_date = "2024-08-01"
    end_date = "2024-09-30"
    
    # Test all export types
    export_types = ["regular", "mu", "oc", "pf"]
    
    for data_type in export_types:
        print(f"\nTesting export type: {data_type}")
        
        # Make the request
        url = f"{BASE_URL}/api/dashboard/excel/?start={start_date}&end={end_date}&type={data_type}"
        
        try:
            response = requests.get(url)
            
            # Check if request was successful
            if response.status_code == 200:
                # Get the filename from Content-Disposition header
                content_disposition = response.headers.get('Content-Disposition', '')
                print(f"Content-Disposition: {content_disposition}")
                
                # Extract filename
                import re
                match = re.search(r'filename="([^"]+)"', content_disposition)
                if match:
                    filename = match.group(1)
                    print(f"Extracted filename: {filename}")
                    
                    # Check if filename matches expected format
                    expected_filename = f"{data_type}_{start_date}_{end_date}.xlsx"
                    if filename == expected_filename:
                        print(f"✅ SUCCESS: Filename format is correct for {data_type}!")
                    else:
                        print(f"❌ ERROR: Filename format is incorrect for {data_type}. Expected: {expected_filename}, Got: {filename}")
                else:
                    print("❌ ERROR: Could not extract filename from Content-Disposition header")
                
                # Check content type
                content_type = response.headers.get('Content-Type', '')
                if 'spreadsheetml' in content_type:
                    print(f"✅ SUCCESS: Response is an Excel file for {data_type}!")
                else:
                    print(f"❌ ERROR: Expected Excel file but got {content_type} for {data_type}")
            else:
                print(f"❌ ERROR: Request failed with status code {response.status_code} for {data_type}")
                print(f"Response: {response.text[:100]}...")
        except Exception as e:
            print(f"❌ ERROR: {str(e)} for {data_type}")

if __name__ == "__main__":
    test_unified_export()