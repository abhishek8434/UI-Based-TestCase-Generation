from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from jira.jira_client import fetch_issue
from azure_integration.azure_client import AzureClient
from ai.generator import generate_test_case
from utils.file_handler import save_test_script, save_excel_report
import os

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/results')
def results():
    return render_template('results.html')

# Remove the /preview and /api/preview routes as they're no longer needed

@app.route('/api/generate', methods=['POST'])
def generate():
    try:
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
                
                # Generate test cases from image
                test_cases = generate_test_case_from_image(image_path)
                
                if not test_cases:
                    os.remove(image_path)  # Clean up if generation fails
                    return jsonify({'error': 'Failed to generate test cases from image'}), 400
                
                # Save test case files
                file_base_name = f'test_image_{unique_id}'
                txt_file = save_test_script(test_cases, file_base_name)
                excel_file = save_excel_report(test_cases, file_base_name)
                
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
                    return jsonify({'error': 'Failed to save test case files'}), 400
                    
            except Exception as e:
                if os.path.exists(image_path):
                    os.remove(image_path)
                return jsonify({'error': str(e)}), 500
                
        else:
            # Existing Jira/Azure logic
            data = request.json
            
            # Set environment variables from frontend input
            os.environ['JIRA_URL'] = data.get('jiraUrl', '')
            os.environ['JIRA_USER'] = data.get('jiraUser', '')
            os.environ['JIRA_API_TOKEN'] = data.get('jiraToken', '')
            os.environ['AZURE_DEVOPS_URL'] = data.get('azureUrl', '')
            os.environ['AZURE_DEVOPS_PAT'] = data.get('azurePat', '')
            os.environ['AZURE_DEVOPS_ORG'] = data.get('azureOrg', '')
            os.environ['AZURE_DEVOPS_PROJECT'] = data.get('azureProject', '')
            
            source_type = data.get('sourceType', 'jira')
            item_ids = data.get('itemId', [])
            
            if isinstance(item_ids, str):
                item_ids = [item_ids]
            
            results = {}
            
            for item_id in item_ids:
                if source_type == 'jira':
                    issue = fetch_issue(item_id)
                    if not issue:
                        continue
                    
                    test_cases = generate_test_case(issue['fields']['summary'], 
                                                  issue['fields']['description'])
                else:  # azure
                    azure_client = AzureClient()
                    work_items = azure_client.fetch_azure_work_items([item_id])
                    if not work_items or not work_items[0]:
                        continue
                        
                    work_item = work_items[0]  # Get the first work item
                    test_cases = generate_test_case(work_item['title'], 
                                                  work_item['description'])
                
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
            
            if not results:
                return jsonify({'error': 'Failed to generate test cases for any items'}), 400
                
            return jsonify({
                'success': True,
                'files': results
            })
            
    except Exception as e:
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
            df = pd.read_excel(file_path)
            return jsonify({
                'content': df.to_dict('records')
            })
        else:
            with open(file_path, 'r') as f:
                content = f.read()
            return jsonify({'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 404

if __name__ == '__main__':
    app.run(debug=True)