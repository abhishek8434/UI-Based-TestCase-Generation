from openai import OpenAI
from config.settings import OPENAI_API_KEY
from typing import Optional, List
import base64
import requests
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenAI client once at module level
client = OpenAI(api_key=OPENAI_API_KEY)

def encode_image_from_url(image_url: str) -> Optional[str]:
    """Encode image from URL to base64."""
    try:
        response = requests.get(image_url)
        return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        logger.error(f"Error encoding image from URL: {e}")
        return None

def encode_image_from_path(image_path: str) -> Optional[str]:
    """Encode image from local path to base64."""
    try:
        with open(image_path, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        logger.error(f"Error encoding image from path: {e}")
        return None

def get_test_type_config(test_type: str) -> dict:
    """Get the configuration for a specific test type"""
    base_configs = {
        "dashboard_functional": {
            "prefix": "TC_FUNC",
            "description": "functional test cases focusing on valid inputs and expected behaviors",
            "count": 20
        },
        "dashboard_negative": {
            "prefix": "TC_NEG",
            "description": "negative test cases focusing on invalid inputs, error handling, and edge cases",
            "count": 20
        },
        "dashboard_ui": {
            "prefix": "TC_UI",
            "description": "UI test cases focusing on visual elements and layout",
            "count": 15
        },
        "dashboard_ux": {
            "prefix": "TC_UX",
            "description": "user experience test cases focusing on user interaction and workflow",
            "count": 15
        },
        "dashboard_compatibility": {
            "prefix": "TC_COMPAT",
            "description": "compatibility test cases across different browsers and platforms",
            "count": 15
        },
        "dashboard_performance": {
            "prefix": "TC_PERF",
            "description": "performance test cases focusing on load times and responsiveness",
            "count": 15
        }
    }
    return base_configs.get(test_type, {})

def generate_test_case_from_image(image_path: str, selected_types: List[str] = None) -> Optional[str]:
    """Generate test cases from an image using OpenAI Vision API"""
    if not image_path:
        logger.error("No image path provided for test case generation")
        return None

    if not selected_types or len(selected_types) == 0:
        logger.error("No test types selected for test case generation")
        return None

    logger.info(f"Generating test cases from image for types: {selected_types}")
    logger.info(f"Image path: {image_path}")
    
    # List of models to try in order of preference
    vision_models = ["gpt-4o", "gpt-4-vision"]
    
    try:
        base64_image = encode_image_from_path(image_path)
        if not base64_image:
            logger.error("Failed to encode image")
            return None

        all_test_cases = []
        
        for test_type in selected_types:
            logger.info(f"Starting generation for test type: {test_type}")
            config = get_test_type_config(test_type)
            if not config:
                logger.warning(f"Skipping unknown test type: {test_type}")
                continue

            prompt = f"""
            Analyze the image and generate EXACTLY {config['count']} {test_type} test cases.
            Focus ONLY on {config['description']}.

            Use this EXACT format for each test case:

            Title: {config['prefix']}_[Number]_[Brief_Title]
            Scenario: [Detailed scenario description based on the image]
            Steps to reproduce:
            1. [Step 1]
            2. [Step 2]
            ...
            Expected Result: [What should happen]
            Actual Result: [To be filled during execution]
            Priority: [High/Medium/Low]
            """

            test_cases = None
            last_error = None
            
            # Try each model in sequence until one works
            for model in vision_models:
                if test_cases:
                    break  # Already succeeded with a previous model
                
                try:
                    logger.info(f"Sending request to OpenAI Vision API using model {model} for {test_type} test cases")
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": f"You are a QA engineer generating {config['count']} {test_type} test cases from the provided image. Use {config['prefix']} as the prefix."
                            },
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{base64_image}"
                                        }
                                    }
                                ]
                            }
                        ],
                        max_tokens=2000
                    )
                    
                    test_cases = response.choices[0].message.content.strip()
                    if test_cases:
                        logger.info(f"Successfully generated {test_type} test cases using model {model}")
                        # Add a section header for each test type to help with parsing
                        test_cases_with_header = f"TEST TYPE: {test_type}\n\n{test_cases}"
                        all_test_cases.append(test_cases_with_header)
                        break
                    else:
                        logger.warning(f"Received empty response for {test_type} test cases using model {model}")
                
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Error using model {model} for {test_type} test cases: {last_error}")
                    continue  # Try next model
            
            # If all models failed, log the last error
            if not test_cases and last_error:
                # Provide more detailed error message for common errors
                if "model_not_found" in last_error or "invalid_request_error" in last_error:
                    logger.error(f"Error with all OpenAI models for {test_type} test cases: {last_error}")
                    logger.error("Please check if your OpenAI account has access to GPT-4 models with vision capabilities.")
                elif "authorization" in last_error.lower() or "api key" in last_error.lower():
                    logger.error(f"Authorization error for {test_type} test cases: {last_error}")
                    logger.error("Please check your OpenAI API key in the configuration.")
                else:
                    logger.error(f"Error generating {test_type} test cases with all models: {last_error}")
                
            logger.info(f"Completed generation for test type: {test_type}")

        if not all_test_cases:
            logger.error("Failed to generate any test cases")
            return None

        logger.info(f"Successfully generated test cases for {len(all_test_cases)} test types")
        return "\n\n" + "\n\n".join(all_test_cases)

    except Exception as e:
        logger.error(f"Error in generate_test_case_from_image: {str(e)}")
        return None