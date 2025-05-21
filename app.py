from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from jira.jira_client import fetch_issue
from azure_integration.azure_client import AzureClient
from ai.generator import generate_test_case
from utils.file_handler import save_test_script, save_excel_report
import os
import logging
from utils.mongo_handler import MongoHandler

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
                
                # Mark all test types as completed
                with generation_status['lock']:
                    generation_status['completed_types'] = generation_status['total_types'].copy()
                    generation_status['is_generating'] = False
                
                if txt_file and excel_file:
                    return jsonify({
                        'success': True,
                        'files': {
                            'image': {
                                'txt': txt_file,
                                'excel': excel_file,
                                'source_image': stored_filename
                            }
                        }
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
                    issue = fetch_issue(item_id)
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
                    # Initialize Azure client
                    azure_client = AzureClient()
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
                
            return jsonify({
                'success': True,
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
        return send_file(file_path, as_attachment=True)
    except Exception as e:
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

# Update the generation status endpoint
@app.route('/api/generation-status')
def check_generation_status():
    try:
        # Get item IDs from query string if provided
        item_ids = request.args.get('items', '').split(',')
        item_ids = [item_id for item_id in item_ids if item_id]  # Filter empty items
        
        with generation_status['lock']:
            is_generating = generation_status['is_generating']
            total = len(generation_status['total_types']) 
            completed = len(generation_status['completed_types'])
            completed_types_list = list(generation_status['completed_types'])
            
            # Ensure progress percentage is calculated properly
            progress_percentage = (completed / total * 100) if total > 0 else 0
        
        # Check if files exist for the requested items
        files_ready = False
        if item_ids and not is_generating:
            files_ready = True
            for item_id in item_ids:
                safe_filename = ''.join(c for c in item_id if c.isalnum() or c in ('-', '_'))
                file_base_name = f'test_{safe_filename}'
                
                txt_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', f'{file_base_name}.txt')
                excel_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', f'{file_base_name}.xlsx')
                
                if not (os.path.exists(txt_path) and os.path.exists(excel_path)):
                    files_ready = False
                    logger.warning(f"Files not ready for {item_id}: txt={os.path.exists(txt_path)}, excel={os.path.exists(excel_path)}")
                    break
                    
            logger.info(f"Generation status check - is_generating: {is_generating}, completed: {completed}/{total}, files_ready: {files_ready}")
        else:
            logger.info(f"Generation status check - is_generating: {is_generating}, completed: {completed}/{total}, no items checked")
    
        return jsonify({
            'is_generating': is_generating,
            'completed_types': completed,
            'total_types': total,
            'completed_test_types': completed_types_list,
            'progress_percentage': progress_percentage,
            'files_ready': files_ready
        })
    except Exception as e:
        logger.error(f"Error in generation status check: {str(e)}", exc_info=True)
        return jsonify({
            'is_generating': False,
            'error': str(e),
            'files_ready': False
        }), 500

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
    return render_template('view.html', test_case=test_case)

@app.route('/api/shared/excel/<url_key>')
def download_shared_excel(url_key):
    try:
        # Get the test case data from MongoDB
        test_case = mongo_handler.get_test_case(url_key)
        if not test_case:
            return jsonify({'error': 'Test case not found'}), 404
        
        # Generate filename based on item_id or use generic name
        if test_case.get('item_id'):
            file_base_name = f"test_{test_case['item_id']}"
        else:
            file_base_name = f"test_shared_{url_key[:8]}"
        
        # Format test data properly for Excel generation
        test_data = test_case['test_data']
        
        # If test_data is already in array format, format it for Excel
        if isinstance(test_data, list):
            import json
            formatted_data = ""
            
            for tc in test_data:
                formatted_data += "TEST CASE:\n"
                if 'Title' in tc:
                    formatted_data += f"Title: {tc.get('Title', '')}\n"
                if 'Scenario' in tc:
                    formatted_data += f"Scenario: {tc.get('Scenario', '')}\n"
                if 'Steps' in tc:
                    steps = tc.get('Steps', '')
                    if isinstance(steps, list):
                        formatted_data += "Steps:\n" + "\n".join([f"- {step}" for step in steps])
                    else:
                        formatted_data += f"Steps: {steps}\n"
                if 'Expected Result' in tc:
                    formatted_data += f"Expected Result: {tc.get('Expected Result', '')}\n"
                formatted_data += "\n\n"
            
            test_data_str = formatted_data
        else:
            # Convert test data to string if it's in other JSON format
            import json
            test_data_str = json.dumps(test_data, indent=2)
        
        # Generate Excel file
        from utils.file_handler import save_excel_report
        excel_file = save_excel_report(test_data_str, file_base_name)
        
        if not excel_file:
            return jsonify({'error': 'Failed to generate Excel file'}), 500
        
        # Return the Excel file
        file_path = os.path.join(os.path.dirname(__file__), 'tests', 'generated', excel_file)
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        logger.error(f"Error generating Excel file: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)