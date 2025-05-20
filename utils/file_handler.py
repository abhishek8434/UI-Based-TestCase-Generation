import os
from typing import Optional
import logging
import pandas as pd

def save_test_script(content: str, base_name: str) -> Optional[str]:
    """Save test script to file.

    Args:
        content (str): Content to write to file
        base_name (str): Base name for the file

    Returns:
        Optional[str]: Filename if successful, None otherwise
    """
    if not base_name or not content:
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
        return filename
    except Exception as e:
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
        print("❌ Filename and test cases cannot be empty")
        return None

    filename = f"{base_name}.xlsx"
    output_dir = os.path.join("tests", "generated")
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    try:
        # Parse test cases into structured data
        test_data = []
        current_test = {}
        current_section = "General"

        for line in test_cases.split('\n'):
            line = line.strip()
            if not line:
                continue

            # Handle section headers
            if line.startswith('###'):
                current_section = line.replace('#', '').strip() or "General"
                continue

            # Handle both Jira/Azure and image test case formats
            if line.startswith('**Title:**') or line.startswith('Title:'):
                if current_test:
                    test_data.append(current_test)
                current_test = {
                    'Section': current_section or "General",
                    'Title': line.replace('**Title:**', '').replace('Title:', '').strip()
                }
            elif line.startswith('**Scenario:**') or line.startswith('Scenario:'):
                current_test['Scenario'] = line.replace('**Scenario:**', '').replace('Scenario:', '').strip()
            elif line.startswith('**Steps to reproduce:**') or line.startswith('Steps to reproduce:'):
                current_test['Steps'] = []
            elif line.startswith(tuple(f"{i}." for i in range(1, 10))) and 'Steps' in current_test:
                step = line.split('.', 1)[1].strip()
                current_test['Steps'].append(step)
            elif line.startswith('**Expected Result:**') or line.startswith('Expected Result:'):
                current_test['Expected Result'] = line.replace('**Expected Result:**', '').replace('Expected Result:', '').strip()
            elif line.startswith('**Actual Result:**') or line.startswith('Actual Result:'):
                current_test['Actual Result'] = line.replace('**Actual Result:**', '').replace('Actual Result:', '').strip()

        # Add the last test case
        if current_test:
            test_data.append(current_test)

        # Convert to DataFrame
        df = pd.DataFrame(test_data)
        
        # Fill empty values with appropriate defaults
        df['Section'] = df['Section'].fillna('General')
        df['Scenario'] = df['Scenario'].fillna('')
        df['Steps'] = df['Steps'].apply(lambda x: '\n'.join(x) if isinstance(x, list) else '')
        df['Expected Result'] = df['Expected Result'].fillna('')
        df['Actual Result'] = df['Actual Result'].fillna('')

        # Reorder columns
        column_order = ['Section', 'Title', 'Scenario', 'Steps', 'Expected Result', 'Actual Result']
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

        return filename

    except Exception as e:
        print(f"❌ Error saving Excel report: {e}")
        return None
