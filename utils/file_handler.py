import os
from typing import Optional, List, Dict
import logging
import pandas as pd
import re

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def save_test_script(content: str, base_name: str) -> Optional[str]:
    """Save test script to file.

    Args:
        content (str): Content to write to file
        base_name (str): Base name for the file

    Returns:
        Optional[str]: Filename if successful, None otherwise
    """
    if not base_name or not content:
        logger.error("Filename and content cannot be empty")
        print("❌ Filename and content cannot be empty")
        return None

    output_dir = os.path.join("tests", "generated")
    # Ensure the filename is valid
    filename = f"{base_name}.txt"
    file_path = os.path.join(output_dir, filename)

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)
        logger.info(f"Test script saved successfully: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Error saving file {filename}: {e}")
        print(f"❌ Error saving file {filename}: {e}")
        return None

def save_excel_report(test_cases: str, base_name: str) -> Optional[str]:
    """Save test cases to Excel file.

    Args:
        test_cases (str): Test cases content to write to Excel
        base_name (str): Base name for the file

    Returns:
        Optional[str]: Filename if successful, None otherwise
    """
    if not base_name or not test_cases:
        logger.error("Filename and test cases cannot be empty")
        print("❌ Filename and test cases cannot be empty")
        return None

    filename = f"{base_name}.xlsx"
    output_dir = os.path.join("tests", "generated")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    try:
        # First, check for TEST TYPE section markers
        sections = extract_test_type_sections(test_cases)
        
        # If no explicit TEST TYPE sections found, use traditional parsing
        if not sections:
            logger.info("No TEST TYPE sections found, using traditional parsing")
            test_data = parse_traditional_format(test_cases)
        else:
            # Parse each section separately
            logger.info(f"Found {len(sections)} TEST TYPE sections")
            test_data = []
            for section_name, section_content in sections.items():
                section_data = parse_traditional_format(section_content, default_section=section_name)
                test_data.extend(section_data)

        # Convert to DataFrame
        df = pd.DataFrame(test_data) if test_data else pd.DataFrame()
        
        if df.empty:
            logger.warning("No test cases could be parsed")
            print("⚠️ No test cases could be parsed")
            # Create a minimal DataFrame to avoid errors
            df = pd.DataFrame({
                'Section': ['No test cases found'],
                'Title': ['No test cases could be parsed'],
                'Scenario': [''],
                'Steps': [''],
                'Expected Result': [''],
                'Actual Result': ['']
            })
        
        # Fill empty values with appropriate defaults
        if 'Section' not in df.columns:
            df['Section'] = 'General'
        else:
            df['Section'] = df['Section'].fillna('General')
            
        # Ensure all required columns exist
        required_columns = ['Title', 'Scenario', 'Steps', 'Expected Result', 'Actual Result', 'Status', 'Priority']
        for col in required_columns:
            if col not in df.columns:
                df[col] = ''
                
        # Convert list of steps to string if needed        
        if 'Steps' in df.columns:            
            df['Steps'] = df['Steps'].apply(lambda x: '\n'.join([f"{i+1}. {step}" for i, step in enumerate(x)]) if isinstance(x, list) else (x or ''))
        
        # Fill all remaining NaN values
        df = df.fillna('')

        # Reorder columns, starting with the most important ones
        column_order = ['Section', 'Title', 'Scenario', 'Steps', 'Expected Result', 'Status', 'Actual Result', 'Priority']
        # Add any additional columns that might be present
        for col in df.columns:
            if col not in column_order:
                column_order.append(col)
                
        # Keep only columns that exist in the DataFrame
        column_order = [col for col in column_order if col in df.columns]
        df = df.reindex(columns=column_order)

        # Save to Excel with improved formatting
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Test Cases')
            worksheet = writer.sheets['Test Cases']
            
            # Adjust column widths
            for idx, col in enumerate(df.columns):
                max_length = max(
                    df[col].astype(str).apply(len).max(),
                    len(col)
                )
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_length + 2, 50)

        logger.info(f"Excel report saved successfully: {filename}")
        return filename

    except Exception as e:
        logger.error(f"Error saving Excel report: {e}")
        print(f"❌ Error saving Excel report: {e}")
        return None

def extract_test_type_sections(test_cases: str) -> Dict[str, str]:
    """Extract sections from test cases content based on TEST TYPE markers.
    
    Args:
        test_cases (str): The full test cases content
        
    Returns:
        Dict[str, str]: Dictionary of section name to section content
    """
    sections = {}
    
    # Look for TEST TYPE: section markers
    section_pattern = r"TEST TYPE:\s*([^\n]+)"
    section_matches = list(re.finditer(section_pattern, test_cases))
    
    # If no TEST TYPE markers found, return empty dict
    if not section_matches:
        return {}
    
    # Process each section
    for i, match in enumerate(section_matches):
        section_name = match.group(1).strip()
        start_pos = match.end()
        # End position is either the start of next section or end of content
        end_pos = section_matches[i+1].start() if i+1 < len(section_matches) else len(test_cases)
        # Extract section content
        section_content = test_cases[start_pos:end_pos].strip()
        sections[section_name] = section_content
        
    return sections

def parse_traditional_format(test_cases: str, default_section: str = "General") -> List[Dict]:
    """Parse test cases using the traditional format.
    
    Args:
        test_cases (str): Test cases content to parse
        default_section (str): Default section name if none is specified
        
    Returns:
        List[Dict]: List of test case dictionaries
    """
    test_data = []
    current_test = {}
    current_section = default_section
    collecting_steps = False
    current_steps = []

    # Split by lines for easier processing
    lines = test_cases.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        
        if not line:
            continue
            
        # Check for section headers (both markdown and plain text)
        if line.startswith('###'):
            current_section = line.replace('#', '').strip() or default_section
            continue
            
        # Handle various title formats
        title_match = re.match(r'^(?:\d+\.\s*)?(?:\*\*)?Title:(?:\*\*)?\s*(.*?)$', line)
        if title_match:
            # Save previous test case if exists
            if current_test:
                if collecting_steps:
                    current_test['Steps'] = current_steps
                test_data.append(current_test)
                
            # Start a new test case
            current_test = {
                'Section': current_section,
                'Title': title_match.group(1).strip()
            }
            collecting_steps = False
            current_steps = []
            continue
            
        # Handle scenario
        scenario_match = re.match(r'^(?:\*\*)?Scenario:(?:\*\*)?\s*(.*?)$', line)
        if scenario_match and current_test:
            current_test['Scenario'] = scenario_match.group(1).strip()
            continue
            
        # Handle steps header - support multiple variations
        steps_match = re.match(r'^(?:\*\*)?Steps(?: to reproduce)?:(?:\*\*)?', line)
        if steps_match and current_test:
            collecting_steps = True
            current_steps = []
            continue
            
        # Collect steps
        if collecting_steps:
            # Support various step formats (1. Step, - Step, * Step, etc.)
            step_match = re.match(r'^(?:(\d+)\.|\-|\*)\s*(.*?)$', line)
            if step_match:
                step_text = step_match.group(2) if step_match.groups()[-1] else line
                current_steps.append(step_text.strip())
                continue
                
        # Handle expected result
        expected_match = re.match(r'^(?:\*\*)?Expected Result:(?:\*\*)?\s*(.*?)$', line)
        if expected_match and current_test:
            current_test['Expected Result'] = expected_match.group(1).strip()
            continue
            
        # Handle actual result
        actual_match = re.match(r'^(?:\*\*)?Actual Result:(?:\*\*)?\s*(.*?)$', line)
        if actual_match and current_test:
            current_test['Actual Result'] = actual_match.group(1).strip()
            continue
            
        # Handle status
        status_match = re.match(r'^(?:\*\*)?Status:(?:\*\*)?\s*(.*?)$', line)
        if status_match and current_test:
            current_test['Status'] = status_match.group(1).strip()
            continue
            
        # Handle priority
        priority_match = re.match(r'^(?:\*\*)?Priority:(?:\*\*)?\s*(.*?)$', line)
        if priority_match and current_test:
            current_test['Priority'] = priority_match.group(1).strip()
            continue
            
    # Add the last test case if exists
    if current_test:
        if collecting_steps:
            current_test['Steps'] = current_steps
        test_data.append(current_test)
        
    return test_data