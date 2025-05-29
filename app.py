from flask import Flask, request, jsonify, send_file, render_template, after_this_request
from flask_cors import CORS
from jira.jira_client import fetch_issue
from azure_integration.azure_client import AzureClient
from ai.generator import generate_test_case
from utils.file_handler import save_test_script, save_excel_report
import os
import json
import logging
# Add at the top of the file
from utils.mongo_handler import MongoHandler
import datetime

app = Flask(__name__)
CORS(app)

# Add this logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/results')
def results():
    return render_template('results.html')


# Add at the top of the file with other imports
from threading import Lock

# Add after app initialization
generation_status = {
    'is_generating': False,
    'completed_types': set(),
    'total_types': set(),
    'lock': Lock()
}

# Modify the generate endpoint
@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        data = request.json if request.is_json else request.form
        
        # Get test case types with proper fallback
        selected_types = []
        if request.is_json:
            selected_types = data.get('testCaseTypes[]', data.get('testCaseTypes', []))
        else:
            selected_types = data.getlist('testCaseTypes[]')
            
        # Ensure selected_types is always a list
        if isinstance(selected_types, str):
            selected_types = [selected_types]
            
        # Validate test case types
        if not selected_types:
            return jsonify({'error': 'Please select at least one test case type'}), 400

        # Update generation status
        with generation_status['lock']:
            generation_status['is_generating'] = True
            generation_status['completed_types'] = set()
            generation_status['total_types'] = set(selected_types)

        # Log the request for debugging
        logger.info(f"Generation request - Types: {selected_types}")
        
        source_type = request.form.get('sourceType') if request.form else request.json.get('sourceType')
        
        if source_type == 'image':
            # Handle image upload
            if 'imageFile' not in request.files:
                return jsonify({'error': 'No image file uploaded'}), 400
                
            image_file = request.files['imageFile']
            if image_file.filename == '':
                return jsonify({'error': 'No selected file'}), 400
                
            # Create unique identifier for the image
            import uuid
            import datetime
            unique_id = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"
            
            # Save the uploaded image in a permanent storage
            image_storage = os.path.join(os.path.dirname(__file__), 'tests', 'images')
            os.makedirs(image_storage, exist_ok=True)
            
            # Get file extension
            file_ext = os.path.splitext(image_file.filename)[1]
            stored_filename = f"image_{unique_id}{file_ext}"
            image_path = os.path.join(image_storage, stored_filename)
            
            # Save the image
            image_file.save(image_path)
            
            try:
                # Import the image generator
                from ai.image_generator import generate_test_case_from_image
                
                # Get selected test case types
                selected_types = request.form.getlist('testCaseTypes[]')
                if not selected_types:
                    os.remove(image_path)  # Clean up if validation fails
                    # Reset the generation status
                    with generation_status['lock']:
                        generation_status['is_generating'] = False
                    return jsonify({'error': 'Please select at least one test case type'}), 400
                
                # Generate test cases from image - one type at a time
                test_cases = None
                all_types_processed = True
                error_messages = []
                
                for test_type in selected_types:
                    try:
                        # Generate one type at a time
                        logger.info(f"Generating {test_type} test cases from image")
                        type_test_case = generate_test_case_from_image(
                            image_path,
                            selected_types=[test_type]
                        )
                        
                        if type_test_case:
                            if test_cases:
                                test_cases += "\n\n" + type_test_case
                            else:
                                test_cases = type_test_case
                                
                            # Mark this type as completed
                            with generation_status['lock']:
                                generation_status['completed_types'].add(test_type)
                        else:
                            error_messages.append(f"Failed to generate {test_type} test cases from image")
                            logger.error(f"Failed to generate {test_type} test cases from image")
                            all_types_processed = False
                    except Exception as e:
                        error_messages.append(f"Error generating {test_type} test cases: {str(e)}")
                        logger.error(f"Error generating {test_type} test cases from image: {str(e)}", exc_info=True)
                        all_types_processed = False
                
                if not test_cases:
                    os.remove(image_path)  # Clean up if generation fails
                    # Reset the generation status
                    with generation_status['lock']:
                        generation_status['is_generating'] = False
                    
                    # Provide better error message
                    error_message = "Failed to generate test cases from image"
                    if error_messages:
                        error_message += f": {error_messages[0]}"
                        # Check for common error patterns
                        for msg in error_messages:
                            if "model_not_found" in msg:
                                error_message = "The OpenAI model required for image processing is not available or has been deprecated. Please check your OpenAI account access."
                                break
                            elif "api key" in msg.lower() or "authorization" in msg.lower():
                                error_message = "OpenAI API authentication failed. Please check your API key configuration."
                                break
                    
                    return jsonify({'error': error_message}), 400
                
                # Save test case files
                file_base_name = f'test_image_{unique_id}'
                txt_file = save_test_script(test_cases, file_base_name)
                excel_file = save_excel_report(test_cases, file_base_name)
                
                if txt_file and excel_file:
                    results = {
                        'txt': txt_file,
                        'excel': excel_file
                    }
                    
                    # Inside the image upload handler, before saving to MongoDB
                    formatted_test_cases = []
                    for idx, test_case in enumerate(test_cases.split('\n\n')):
                        if test_case.strip():
                            # Start test case IDs from 2 instead of 1
                            test_case_id = f"TC_KAN-1_{idx + 2}"
                            formatted_test_cases.append({
                                'test_case_id': test_case_id,
                                'content': test_case,
                                'status': ''
                            })
                        
                    # Create MongoDB handler and save test case data
                    mongo_handler = MongoHandler()
                    url_key = mongo_handler.save_test_case({
                        'files': results,
                        'test_cases': formatted_test_cases,
                        'source_type': 'image',
                        'image_id': unique_id
                    }, unique_id)
                    
                    # Mark all test types as completed
                    with generation_status['lock']:
                        generation_status['completed_types'] = generation_status['total_types'].copy()
                        generation_status['is_generating'] = False
                    
                    return jsonify({
                        'success': True,
                        'url_key': url_key,
                        'files': results
                    })
                else:
                    os.remove(image_path)  # Clean up if saving fails
                    # Reset the generation status
                    with generation_status['lock']:
                        generation_status['is_generating'] = False
                    return jsonify({'error': 'Failed to save test case files'}), 400
                    
            except Exception as e:
                if os.path.exists(image_path):
                    os.remove(image_path)
                # Reset the generation status
                with generation_status['lock']:
                    generation_status['is_generating'] = False
                return jsonify({'error': str(e)}), 500
                
        else:
            # Existing Jira/Azure logic
            data = request.json
            source_type = data.get('sourceType', 'jira')
            item_ids = data.get('itemId', [])
            
            # Fix test case types handling for JSON requests
            selected_types = data.get('testCaseTypes[]', data.get('testCaseTypes', []))
            if isinstance(selected_types, str):
                selected_types = [selected_types]
            
            if not selected_types:
                return jsonify({'error': 'Please select at least one test case type'}), 400
            
            if isinstance(item_ids, str):
                item_ids = [item_ids]
            
            results = {}
            all_types_processed = True
            
            for item_id in item_ids:
                test_cases = None
                
                if source_type == 'jira':
                    # Get Jira configuration from request data
                    jira_config = data.get('jira_config')
                    issue = fetch_issue(item_id, jira_config)
                    if not issue:
                        continue
                    
                    for test_type in selected_types:
                        try:
                            # Generate one type at a time
                            type_test_case = generate_test_case(
                                description=issue['fields']['description'],
                                summary=issue['fields']['summary'],
                                selected_types=[test_type]
                            )
                            
                            if type_test_case:
                                if test_cases:
                                    test_cases += "\n\n" + type_test_case
                                else:
                                    test_cases = type_test_case
                                    
                                # Mark this type as completed
                                with generation_status['lock']:
                                    generation_status['completed_types'].add(test_type)
                            else:
                                all_types_processed = False
                                
                        except Exception as e:
                            logger.error(f"Error generating {test_type} test cases: {str(e)}")
                            all_types_processed = False
                            
                elif source_type == 'azure':
                    # Get Azure configuration from request data
                    azure_config = data.get('azure_config')
                    # Only use frontend config if it exists and all required values are present
                    if azure_config and all(azure_config.values()):
                        azure_client = AzureClient(azure_config)
                    else:
                        azure_client = AzureClient()  # Fall back to environment variables
                    work_items = azure_client.fetch_azure_work_items([item_id])
                    
                    if not work_items or len(work_items) == 0:
                        continue
                    
                    # Define work_item from the fetched items
                    work_item = work_items[0]
                    
                    for test_type in selected_types:
                        try:
                            # Generate one type at a time
                            type_test_case = generate_test_case(
                                description=work_item['description'],
                                summary=work_item['title'],
                                selected_types=[test_type]
                            )
                            
                            if type_test_case:
                                if test_cases:
                                    test_cases += "\n\n" + type_test_case
                                else:
                                    test_cases = type_test_case
                                    
                                # Mark this type as completed
                                with generation_status['lock']:
                                    generation_status['completed_types'].add(test_type)
                            else:
                                all_types_processed = False
                                
                        except Exception as e:
                            logger.error(f"Error generating {test_type} test cases: {str(e)}")
                            all_types_processed = False
                
                # Only proceed if test cases were generated
                if not test_cases:
                    continue
                    
                # Save files
                safe_filename = ''.join(c for c in item_id if c.isalnum() or c in ('-', '_'))
                file_base_name = f'test_{safe_filename}'
                
                txt_file = save_test_script(test_cases, file_base_name)
                excel_file = save_excel_report(test_cases, file_base_name)
                
                if txt_file and excel_file:
                    results[item_id] = {
                        'txt': txt_file,
                        'excel': excel_file
                    }
            
            # After all item IDs and types are processed, update generation status
            with generation_status['lock']:
                generation_status['is_generating'] = False
            
            if not results:
                return jsonify({'error': 'Failed to generate test cases for any items'}), 400
                
            # Before returning the final response in Jira/Azure handler
            formatted_test_cases = []
            for idx, test_case in enumerate(test_cases.split('\n\n')):
                if test_case.strip():
                    # Start test case IDs from 2 instead of 1
                    test_case_id = f"TC_{item_ids[0]}_{idx + 2}"
                    formatted_test_cases.append({
                        'test_case_id': test_case_id,
                        'content': test_case,
                        'status': ''
                    })
            
            # Create MongoDB handler and save test case data
            mongo_handler = MongoHandler()
            url_key = mongo_handler.save_test_case({
                'files': results,
                'test_cases': formatted_test_cases,
                'source_type': source_type,
                'item_ids': item_ids
            }, item_ids[0] if item_ids else None)
            
            return jsonify({
                'success': True,
                'url_key': url_key,
                'files': results
            })
            
    except Exception as e:
        logger.error(f"Error during generation: {str(e)}", exc_info=True)
        # Reset the generation status in case of errors
        with generation_status['lock']:
            generation_status['is_generating'] = False
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/<path:filename>')
def download_file(filename):
    try:
        file_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        # Check if status values were provided
        status_values = request.args.get('status')
        
        # Log status values for debugging
        if status_values:
            try:
                status_dict = json.loads(status_values)
                logger.info(f"DOWNLOAD FILE: Received {len(status_dict)} status values: {status_dict}")
            except Exception as e:
                logger.error(f"DOWNLOAD FILE: Error parsing status values: {e}")
        else:
            logger.info("DOWNLOAD FILE: No status values provided")
        
        # Check if a custom filename was provided
        custom_filename = request.args.get('filename')
        
        # If it's an Excel file and status values are provided, update the file
        if status_values and filename.endswith('.xlsx'):
            try:
                status_dict = json.loads(status_values)
                logger.info(f"Updating Excel file with status values: {status_dict}")
                
                # Update the Excel file with status values
                import pandas as pd
                df = pd.read_excel(file_path)
                
                # Update each row where Title matches status key
                updated_count = 0
                for index, row in df.iterrows():
                    title = row.get('Title', '')
                    if title and title in status_dict:
                        df.at[index, 'Status'] = status_dict[title]
                        updated_count += 1
                
                logger.info(f"Updated {updated_count} rows with status values")
                
                # Save to a temporary file
                temp_file_path = f"{file_path}.temp.xlsx"
                df.to_excel(temp_file_path, index=False)
                
                # Use the temporary file for download with custom filename if provided
                if custom_filename:
                    response = send_file(temp_file_path, as_attachment=True, download_name=custom_filename)
                else:
                    response = send_file(temp_file_path, as_attachment=True)
                
                # Set up cleanup after request is complete
                @after_this_request
                def remove_temp_file(response):
                    try:
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
                    except Exception as e:
                        logger.error(f"Error removing temp file: {e}")
                    return response
                    
            except Exception as e:
                logger.error(f"Error updating Excel with status values: {e}")
                # Fall back to original file if error occurs
                if custom_filename:
                    response = send_file(file_path, as_attachment=True, download_name=custom_filename)
                else:
                    response = send_file(file_path, as_attachment=True)
        
        # For TXT files with status values
        elif status_values and filename.endswith('.txt'):
            try:
                status_dict = json.loads(status_values)
                logger.info(f"Updating TXT file with status values: {status_dict}")
                
                # Read the original content
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Create a temporary file
                temp_file_path = f"{file_path}.temp.txt"
                
                # Write updated content with status values appended
                with open(temp_file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                    f.write("\n\n# STATUS VALUES\n")
                    for title, status in status_dict.items():
                        if status:  # Only include non-empty status values
                            f.write(f"{title}: {status}\n")
                
                # Use the temporary file for download with custom filename if provided
                if custom_filename:
                    response = send_file(temp_file_path, as_attachment=True, download_name=custom_filename)
                else:
                    response = send_file(temp_file_path, as_attachment=True)
                
                # Set up cleanup after request is complete
                @after_this_request
                def remove_temp_file(response):
                    try:
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
                    except Exception as e:
                        logger.error(f"Error removing temp file: {e}")
                    return response
                    
            except Exception as e:
                logger.error(f"Error updating TXT with status values: {e}")
                # Fall back to original file if error occurs
                if custom_filename:
                    response = send_file(file_path, as_attachment=True, download_name=custom_filename)
                else:
                    response = send_file(file_path, as_attachment=True)
        else:
            # Default case - no status values or not a handled file type
            if custom_filename:
                response = send_file(file_path, as_attachment=True, download_name=custom_filename)
            else:
                response = send_file(file_path, as_attachment=True)
            
        # Add cache control headers to prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return jsonify({'error': str(e)}), 404

@app.route('/api/content/<path:filename>')
def get_file_content(filename):
    try:
        file_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
            
        if filename.endswith('.xlsx'):
            import pandas as pd
            import numpy as np
            import json
            
            # Read the Excel file
            df = pd.read_excel(file_path)
            
            # Get status values if provided
            status_values = request.args.get('status')
            status_dict = {}
            if status_values:
                try:
                    status_dict = json.loads(status_values)
                    logger.info(f"Applying status values to content: {status_dict}")
                except Exception as e:
                    logger.error(f"Error parsing status values: {e}")
            
            # Convert to records and handle NaN values
            records = []
            for index, row in df.iterrows():
                record = {}
                for column in df.columns:
                    value = row[column]
                    # Handle NaN, NaT, and other non-JSON-serializable values
                    if pd.isna(value):
                        record[column] = None
                    else:
                        record[column] = value
                
                # Update status if available
                title = record.get('Title', '')
                if title and title in status_dict:
                    record['Status'] = status_dict[title]
                
                records.append(record)
            
            logger.info(f"Converted Excel file {filename} to {len(records)} records")
            return jsonify({
                'content': records
            })
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({'content': content})
    except Exception as e:
        logger.error(f"Error reading file {filename}: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 404

@app.route('/api/update-status', methods=['POST'])
def update_status():
    try:
        data = request.json
        logger.info(f"Received status update request: {data}")
        
        url_key = data.get('key')
        test_case_id = data.get('test_case_id')
        status = data.get('status')
        is_shared_view = data.get('shared_view', False)

        # Validate required parameters
        if not url_key:
            return jsonify({'error': 'Missing required parameter: key'}), 400
        if not test_case_id:
            return jsonify({'error': 'Missing required parameter: test_case_id'}), 400
        if not status:
            return jsonify({'error': 'Missing required parameter: status'}), 400
            
        # Validate status is not empty string
        if status.strip() == '':
            return jsonify({'error': 'Status cannot be empty'}), 400

        mongo_handler = MongoHandler()
        
        # First verify the document exists
        doc = mongo_handler.collection.find_one({"url_key": url_key})
        if not doc:
            error_msg = f"No document found with url_key: {url_key}"
            logger.error(error_msg)
            return jsonify({'error': error_msg}), 404
        
        # Try to update using the MongoHandler
        success = mongo_handler.update_test_case_status(url_key, test_case_id, status)
        
        if success:
            # Force an update to the status dict and test data array in a single operation
            # This ensures both copies of the data are updated
            result = mongo_handler.collection.update_one(
                {"url_key": url_key},
                {
                    "$set": {
                        f"status.{test_case_id}": status,
                        "status_updated_at": datetime.datetime.now()
                    }
                }
            )
            
            # For shared view, we also need to update the test_data array entries directly
            if is_shared_view and 'test_data' in doc and isinstance(doc['test_data'], list):
                for i, tc in enumerate(doc['test_data']):
                    if tc.get('Title') == test_case_id:
                        # Update the Status field directly in the array
                        mongo_handler.collection.update_one(
                            {"url_key": url_key},
                            {"$set": {f"test_data.{i}.Status": status}}
                        )
                        logger.info(f"Updated status in test_data array index {i}")
                        break
            
            logger.info(f"Successfully updated status for test case '{test_case_id}'")
            return jsonify({'success': True})
        else:
            error_msg = f"Failed to update status for test case {test_case_id} in document {url_key}"
            logger.error(error_msg)
            return jsonify({'error': error_msg}), 404

    except Exception as e:
        logger.error(f"Error updating status: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Initialize MongoDB handler
mongo_handler = MongoHandler()

@app.route('/api/share', methods=['POST'])
def share_test_case():
    try:
        data = request.json
        test_data = data.get('test_data')
        item_id = data.get('item_id')
        if not test_data:
            return jsonify({'error': 'No test data provided'}), 400

        url_key = mongo_handler.save_test_case(test_data, item_id)
        share_url = f"{request.host_url}view/{url_key}"
        
        return jsonify({
            'success': True,
            'share_url': share_url
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/view/<url_key>')
def view_shared_test_case(url_key):
    test_case = mongo_handler.get_test_case(url_key)
    if not test_case:
        return render_template('404.html'), 404
    
    # Make sure the key is included in the test_case object
    test_case['key'] = url_key
    
    return render_template('view.html', test_case=test_case)

@app.route('/api/shared/excel/<url_key>')
def download_shared_excel(url_key):
    try:
        # Get the test case data from MongoDB
        test_case = mongo_handler.get_test_case(url_key)
        if not test_case:
            return jsonify({'error': 'Test case not found'}), 404
        
        # Check if a custom filename was provided in the request
        custom_filename = request.args.get('filename')
        
        # Get status values if provided in the request
        status_values = request.args.get('status')
        status_dict = {}
        if status_values:
            try:
                status_dict = json.loads(status_values)
                logger.info(f"SHARED EXCEL: Received {len(status_dict)} status values: {status_dict}")
            except Exception as e:
                logger.error(f"SHARED EXCEL: Error parsing status values: {e}")
        else:
            logger.info("SHARED EXCEL: No status values provided")
        
        # Generate default filename based on item_id or use generic name if no custom filename
        if not custom_filename:
            if test_case.get('item_id'):
                custom_filename = f"test_{test_case['item_id']}.xlsx"
            else:
                custom_filename = f"test_shared_{url_key[:8]}.xlsx"
        
        # Use item_id for the base name of the generated file
        if test_case.get('item_id'):
            file_base_name = f"test_{test_case['item_id']}"
        else:
            file_base_name = f"test_shared_{url_key[:8]}"
        
        # Format test data properly for Excel generation
        test_data = test_case['test_data']
        
        # Now format for Excel generation
        import json
        formatted_data = ""
        
        # Track which test cases have status updates
        status_updated = set()
        updated_count = 0
        
        for tc in test_data:
            formatted_data += "TEST CASE:\n"
            if 'Title' in tc:
                title = tc.get('Title', '')
                formatted_data += f"Title: {title}\n"
            if 'Scenario' in tc:
                formatted_data += f"Scenario: {tc.get('Scenario', '')}\n"
            
            # Handle steps with special care for arrays
            if 'Steps' in tc:
                steps = tc.get('Steps', '')
                formatted_data += "Steps to reproduce:\n"
                if isinstance(steps, list):
                    for i, step in enumerate(steps):
                        formatted_data += f"{i+1}. {step}\n"
                else:
                    formatted_data += f"1. {steps}\n"
            
            if 'Expected Result' in tc:
                formatted_data += f"Expected Result: {tc.get('Expected Result', '')}\n"
            
            # Explicitly include Status with extra prominence
            # Get from status_dict if available (DOM values), otherwise from test case
            title = tc.get('Title', '')
            status = ''
            if title and title in status_dict:
                status = status_dict[title]
                status_updated.add(title)
                updated_count += 1
            else:
                status = tc.get('Status', '')
            
            # Make sure status is clearly visible
            formatted_data += f"Status: {status}\n\n"
            
            if 'Priority' in tc:
                formatted_data += f"Priority: {tc.get('Priority', '')}\n"
            
            formatted_data += "\n\n"
        
        logger.info(f"SHARED EXCEL: Updated {updated_count} test cases with status values")
        
        # Add a summary of all status values at the end for debugging
        formatted_data += "\n\n# STATUS SUMMARY\n"
        for title, status in status_dict.items():
            if status:
                formatted_data += f"{title}: {status}\n"
        
        test_data_str = formatted_data
        
        # Generate Excel file
        from utils.file_handler import save_excel_report
        excel_file = save_excel_report(test_data_str, file_base_name)
        
        if not excel_file:
            return jsonify({'error': 'Failed to generate Excel file'}), 500
        
        # Return the Excel file with the custom filename
        file_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', excel_file)
        response = send_file(file_path, as_attachment=True, download_name=custom_filename)
        
        # Add aggressive cache control headers to prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Status-Updated-Count"] = str(updated_count)
        response.headers["X-Status-Update-Time"] = str(datetime.datetime.now())
        
        return response
    except Exception as e:
        logger.error(f"Error generating Excel file: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add this after the generate endpoint
@app.route('/api/generation-status')
def get_generation_status():
    try:
        with generation_status['lock']:
            response = {
                'is_generating': generation_status['is_generating'],
                'completed_types': list(generation_status['completed_types']),
                'total_types': list(generation_status['total_types'])
            }
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error getting generation status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/shared-status', methods=['GET'])
def get_shared_status():
    try:
        url_key = request.args.get('key')
        if not url_key:
            return jsonify({'error': 'Missing URL key parameter'}), 400
            
        logger.info(f"Fetching shared status for URL key: {url_key}")
        mongo_handler = MongoHandler()
        
        # Get all status values for the test cases in this document
        # Force refresh from database rather than using cached data
        status_values = mongo_handler.get_test_case_status_values(url_key, force_refresh=True)
        
        if status_values is None:
            return jsonify({'error': 'Test case not found'}), 404
            
        response = jsonify({
            'success': True,
            'status_values': status_values,
            'timestamp': str(datetime.datetime.now())  # Add timestamp for debugging
        })
        
        # Add cache control headers to prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
    except Exception as e:
        logger.error(f"Error retrieving shared status: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/notify-status-change', methods=['GET'])
def notify_status_change():
    try:
        url_key = request.args.get('key')
        test_case_id = request.args.get('testCaseId')
        status = request.args.get('status')
        
        if not url_key:
            return jsonify({'error': 'Missing URL key parameter'}), 400
            
        # Log the notification
        logger.info(f"Received status change notification for key={url_key}, testCaseId={test_case_id}, status={status}")
        
        # Update a special flag in MongoDB to indicate status has changed
        # This can be used to trigger immediate sync in other views
        mongo_handler.collection.update_one(
            {"url_key": url_key},
            {
                "$set": {
                    "status_updated_at": datetime.datetime.now(),
                    "last_status_change": {
                        "test_case_id": test_case_id,
                        "status": status,
                        "timestamp": datetime.datetime.now()
                    }
                }
            }
        )
        
        # Return success with cache control headers
        response = jsonify({
            'success': True,
            'message': 'Status change notification received'
        })
        
        # Add cache control headers to prevent caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        
        return response
    except Exception as e:
        logger.error(f"Error processing status change notification: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/debug/force-sync', methods=['GET'])
def debug_force_sync():
    """Debug endpoint to force sync of status values between views"""
    try:
        url_key = request.args.get('key')
        if not url_key:
            return jsonify({'error': 'Missing URL key parameter'}), 400
            
        logger.info(f"DEBUG: Forcing status sync for URL key: {url_key}")
        mongo_handler = MongoHandler()
        
        # Get the document
        doc = mongo_handler.collection.find_one({"url_key": url_key})
        if not doc:
            return jsonify({'error': 'Document not found'}), 404
            
        # Debug info about the document
        logger.info(f"DEBUG: Document _id: {doc.get('_id')}")
        logger.info(f"DEBUG: Document created_at: {doc.get('created_at')}")
        logger.info(f"DEBUG: Document status dict: {doc.get('status', {})}")
        
        # Check if it's a shared view or main view
        is_shared_view = isinstance(doc.get('test_data'), list)
        logger.info(f"DEBUG: Document is shared view: {is_shared_view}")
        
        # Get current status values from the document
        updated_status = {}
        
        if is_shared_view:
            # Shared view - test_data is a list of test case objects
            for i, tc in enumerate(doc['test_data']):
                title = tc.get('Title', '')
                status = tc.get('Status', '')
                if title:
                    logger.info(f"DEBUG: Shared view TC[{i}]: {title} = {status}")
                    updated_status[title] = status
                    
            # Update all status values in the document too
            # Directly update the status field of each test case in the list
            for i, tc in enumerate(doc['test_data']):
                title = tc.get('Title', '')
                if title in updated_status:
                    logger.info(f"DEBUG: Updating TC[{i}] status: {title} = {updated_status[title]}")
                    mongo_handler.collection.update_one(
                        {"url_key": url_key},
                        {"$set": {f"test_data.{i}.Status": updated_status[title]}}
                    )
        else:
            # Main view - test_data.test_cases is a list of test case objects
            if 'test_data' in doc and 'test_cases' in doc['test_data']:
                for i, tc in enumerate(doc['test_data']['test_cases']):
                    title = tc.get('Title', tc.get('title', ''))
                    status = tc.get('Status', tc.get('status', ''))
                    if title:
                        logger.info(f"DEBUG: Main view TC[{i}]: {title} = {status}")
                        updated_status[title] = status
                        
                # Update all status values in the document too
                # Directly update the status field of each test case in the list
                for i, tc in enumerate(doc['test_data']['test_cases']):
                    title = tc.get('Title', tc.get('title', ''))
                    if title in updated_status:
                        logger.info(f"DEBUG: Updating TC[{i}] status: {title} = {updated_status[title]}")
                        mongo_handler.collection.update_one(
                            {"url_key": url_key},
                            {"$set": {f"test_data.test_cases.{i}.status": updated_status[title]}}
                        )
                    
        # Update the central status dictionary
        if updated_status:
            logger.info(f"DEBUG: Updating status dictionary with {len(updated_status)} values")
            mongo_handler.collection.update_one(
                {"url_key": url_key},
                {"$set": {"status": updated_status}}
            )
            
        # Add a flag to indicate the sync was forced
        mongo_handler.collection.update_one(
            {"url_key": url_key},
            {"$set": {
                "status_force_synced_at": datetime.datetime.now(),
                "status_force_sync_count": doc.get("status_force_sync_count", 0) + 1
            }}
        )
        
        return jsonify({
            'success': True,
            'message': 'Status values forced to sync',
            'status_values': updated_status,
            'is_shared_view': is_shared_view
        })
    except Exception as e:
        logger.error(f"Error during force sync: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)