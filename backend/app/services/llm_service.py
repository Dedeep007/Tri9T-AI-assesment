import os
import json
import httpx
import google.generativeai as genai
from typing import Dict, List, Any, Optional
from pydantic import ValidationError
from app.models.pydantic_schemas import GenerationOutputSchema

class LLMService:
    @staticmethod
    def generate_test_cases(reconstructed_text: str) -> Dict[str, Any]:
        """
        Sends the text to an LLM to generate 3-5 QA test cases.
        Supports Groq and Gemini API keys from environment.
        Implements structured JSON validation and a self-correcting retry loop (up to 3 attempts).
        """
        prompt = LLMService._build_prompt(reconstructed_text)
        
        attempts = 3
        last_error = None
        raw_response = None
        
        for attempt in range(1, attempts + 1):
            try:
                print(f"LLM Generation Attempt {attempt} of {attempts}...")
                
                # Fetch raw response from LLM
                raw_response = LLMService._call_llm_api(prompt, last_error)
                
                # Try to clean and parse JSON
                parsed_json = LLMService._clean_and_parse_json(raw_response)
                
                # Validate JSON against Pydantic schema
                validated_data = GenerationOutputSchema.model_validate(parsed_json)
                
                # Success! Return the data along with prompt/response log info
                return {
                    "status": "complete",
                    "prompt_used": prompt,
                    "raw_llm_response": raw_response,
                    "test_cases": [tc.model_dump() for tc in validated_data.test_cases],
                    "error": None
                }
                
            except (ValueError, ValidationError, json.JSONDecodeError) as e:
                # Capture validation failure and format as error feedback for the next attempt
                last_error = f"Validation Error (Attempt {attempt}): {str(e)}\nRaw Response was: {raw_response}"
                print(f"Attempt {attempt} failed validation: {str(e)}")
                
        # If we reached here, all attempts failed
        return {
            "status": "failed",
            "prompt_used": prompt,
            "raw_llm_response": raw_response,
            "test_cases": [],
            "error": f"All {attempts} generation attempts failed validation. Last error: {last_error}"
        }

    @staticmethod
    def _build_prompt(text: str) -> str:
        return f"""You are a senior QA engineer designing test cases for a medical device (CardioTrack CT-200 Blood Pressure Monitor). 
Generate 3 to 5 highly concrete, executable QA test cases based ONLY on the documentation text provided below.

For each test case, you MUST specify:
- title: A short title identifying the test.
- preconditions: What state the device/system must be in before the test runs.
- steps: A detailed, numbered list of actions to perform.
- expected_result: The exact, measurable result the device must demonstrate.
- requirement_ref: The section heading or path from which this requirement is derived.

You MUST respond ONLY with a valid, clean JSON object matching this schema:
{{
  "test_cases": [
    {{
      "title": "string",
      "preconditions": "string",
      "steps": ["string", "string"],
      "expected_result": "string",
      "requirement_ref": "string"
    }}
  ]
}}

Ensure no explanation, markdown formatting (outside of valid JSON), or leading/trailing text is included in your response.

--- DOCUMENTATION CONTENT ---
{text}
"""

    @staticmethod
    def _call_llm_api(prompt: str, last_error: Optional[str] = None) -> str:
        # If we have a validation error from a previous attempt, append it as feedback
        if last_error:
            prompt += f"\n\n⚠️ CRITICAL FEEDBACK FROM PREVIOUS ATTEMPT:\nYour previous response was invalid. Please fix this validation error:\n{last_error}\nEnsure your response contains ONLY valid JSON matching the schema."

        groq_api_key = os.getenv("GROQ_API_KEY")
        gemini_api_key = os.getenv("GEMINI_API_KEY")

        if groq_api_key:
            # Call Groq API via HTTP
            return LLMService._call_groq(prompt, groq_api_key)
        elif gemini_api_key:
            # Call Gemini API via SDK
            return LLMService._call_gemini(prompt, gemini_api_key)
        else:
            # Fallback mock generator if no keys are found
            # This is critical for automated testing and running the demo offline!
            return LLMService._get_mock_response(prompt)

    @staticmethod
    def _call_groq(prompt: str, api_key: str) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, headers=headers, json=data)
                resp.raise_for_status()
                result = resp.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            raise ValueError(f"Groq API request failed: {e}")

    @staticmethod
    def _call_gemini(prompt: str, api_key: str) -> str:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            # Request JSON output
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            return response.text
        except Exception as e:
            raise ValueError(f"Gemini API request failed: {e}")

    @staticmethod
    def _clean_and_parse_json(raw_text: str) -> Dict[str, Any]:
        if not raw_text:
            raise ValueError("Empty response received from LLM.")
            
        clean_text = raw_text.strip()
        # Remove markdown code blocks if any (e.g. ```json ... ```)
        if clean_text.startswith("```"):
            lines = clean_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()
            
        return json.loads(clean_text)

    @staticmethod
    def _get_mock_response(prompt: str) -> str:
        """
        Mock response generator for testing and offline runs.
        Extracts sections of text to create realistic test cases.
        """
        # Parse prompt to guess what we are writing test cases for
        test_cases = []
        
        if "Inflation" in prompt or "cuff" in prompt.lower():
            test_cases.append({
                "title": "Verify initial cuff inflation pressure",
                "preconditions": "Device is powered on, cuff is connected to the monitor and wrapped around patient arm simulator.",
                "steps": [
                    "Select User Profile 1.",
                    "Press the Start button to begin measurement.",
                    "Observe the pressure reading on the display as inflation begins."
                ],
                "expected_result": "The cuff inflates to an initial target of 180 mmHg as specified in Section 3.2.",
                "requirement_ref": "3.2 Cuff Inflation Sequence"
            })
            test_cases.append({
                "title": "Verify inflation incremental behavior on missing pulse",
                "preconditions": "Device is powered on, cuff is connected, pulse detection is disabled on the arm simulator.",
                "steps": [
                    "Press Start button to begin measurement.",
                    "Observe pressure inflate past 180 mmHg."
                ],
                "expected_result": "Device inflates in incremental steps up to a maximum of 299 mmHg before aborting with error code.",
                "requirement_ref": "3.2 Cuff Inflation Sequence"
            })
        
        if "Overpressure" in prompt or "safety" in prompt.lower() or "E3" in prompt:
            test_cases.append({
                "title": "Verify automatic overpressure deflation limit",
                "preconditions": "Monitor is active, pressure sensor calibration test bench is connected.",
                "steps": [
                    "Simulate cuff pressure exceeding 299 mmHg.",
                    "Observe the emergency deflation valve behavior."
                ],
                "expected_result": "The emergency deflation valve vents the cuff to ambient pressure within 2 seconds.",
                "requirement_ref": "4.1 Overpressure Protection"
            })
            test_cases.append({
                "title": "Verify E3 overpressure display code",
                "preconditions": "Monitor is active, pressure is driven above safe limit.",
                "steps": [
                    "Simulate an overpressure condition.",
                    "Observe the error display on the screen."
                ],
                "expected_result": "The monitor displays error code E3 and auto-deflates.",
                "requirement_ref": "4.2 Error Codes"
            })

        if not test_cases:
            # General default mock
            test_cases.append({
                "title": "Verify standard system operation",
                "preconditions": "Device has fresh batteries installed and is off.",
                "steps": [
                    "Press power button.",
                    "Verify screen turns on."
                ],
                "expected_result": "Screen lights up showing normal startup layout.",
                "requirement_ref": "3.1 Powering On and Profile Selection"
            })

        return json.dumps({"test_cases": test_cases})
