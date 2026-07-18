# SurveyAgent — Technical Implementation Plan

> Achieving **High Feasibility** and **High Maintainability** through Spatial-DOM Mapping + LLM Agent Architecture

To achieve both high feasibility and maintainability, we deliberately avoid two fragile approaches: traditional pixel-level computer vision (OpenCV/YOLO) and costly large vision model (VLM) screenshot recognition.

The chosen deterministic solution is: **Spatial-DOM Mapping via Playwright + DeepSeek V4 Pro Intelligent Agent**.

The core idea: **Use Playwright to convert the web page's "visual-spatial layout and interactive elements" into structured text descriptions, with DeepSeek V4 Pro serving as the reasoning brain for decision-making.** This architecture avoids positioning inaccuracies caused by CSS style changes or AI visual hallucinations, offering exceptional engineering maintainability.

---

## 1. Technology Stack (Production-Ready)

This is currently the most robust, lowest-maintenance combination for building automated web agents:

| Module | Choice | Rationale |
|--------|--------|-----------|
| **Language** | Python 3.11+ | Most mature AI ecosystem, excellent async support, maintainable |
| **Automation Core** | **Playwright for Python** | Faster than Selenium, native async control, excellent support for modern frameworks (React/Vue) dynamic surveys |
| **LLM (Brain)** | **DeepSeek V4 Pro API** | Top-tier reasoning, handles matrix questions and conditional branching effortlessly, extremely stable structured (JSON) output |
| **LLM Driver** | **OpenAI SDK** | Unified interface, minimal abstraction, avoids heavy frameworks (LangChain version fragmentation and maintenance burden) |
| **Parsing & Cleaning** | **BeautifulSoup4** | Simplifies Playwright-captured HTML to reduce token costs |

---

## 2. Architecture & Full Pipeline

The system is organized into four core layers: **Perception → Transformation → Decision → Execution**.

### 2.1 Pipeline Flow

```
[Target Webpage] 
   │ (Playwright loads)
   ▼
[Perception Layer: Spatial DOM Capture] → Inject JS to assign unique interaction IDs 
   │                                      (e.g., data-ui-id="1") to all interactive elements
   ▼
[Transformation Layer: Context Structuring] → Clean HTML, retain only question text,
   │                                          option types, and spatial coordinate info,
   │                                          producing a Layout JSON
   ▼
[Decision Layer: DeepSeek V4 Pro] → Receives Layout JSON + filling requirements,
   │                                 outputs decision instruction JSON
   ▼
[Execution Layer: Playwright Execution] → Locate elements by ui-id,
                                          simulate real mouse clicks/drags/text input
```

### 2.2 Key Technical Challenges

**How to handle highly diverse question types?**

No hard-coded visual recognition per question type — everything is digitized by the transformation layer:

- **Standard single/multi choice**: JS identifies as `<input type="radio/checkbox">`, assigns `ui-id`
- **Matrix / Likert scale**: Parsed as table structure — each row's text and all corresponding button `ui-id`s are bundled into an array and sent to DeepSeek. DeepSeek perfectly understands row-column mapping relationships
- **Slider**: Capture the slider track's bounding box (start coordinate `X1`, end `X2`). DeepSeek decides "slide to 80%", execution layer calculates target coordinate `X_target = X1 + (X2 - X1) * 0.8` and uses Playwright's `mouse.drag_to` to simulate the drag

---

## 3. Implementation Phases

Estimated total: **6 weeks**, 1 senior full-stack/automation engineer + 1 AI engineer (or 1 full-stack covering both).

### Phase 1: Foundation & Spatial Parser (Week 1–2)

- **Goal**: Achieve stable "visual-to-text" transformation with reliable element tagging
- **Milestones**:
  1. Build Playwright base framework: headless/visible mode toggle, cookie persistence, basic anti-detection
  2. Develop core JS injection script (Injector): filter all interactive tags (`input, button, select, [role="slider"]`, etc.), dynamically assign `data-ui-id`
  3. Output clean **Page Layout Snapshot** in concise Markdown or JSON format

### Phase 2: Agent Decision Core & Complex Question Types (Week 3–4)

- **Goal**: Seamless DeepSeek V4 Pro ↔ browser interaction
- **Milestones**:
  1. Design rigorous **System Prompt** constraining DeepSeek to output properly formatted JSON instructions (e.g., `{"thought": "...", "actions": [{"type": "click", "ui_id": "4"}]}`)
  2. Solve matrix, drag-sort, and slider structured mapping logic; stress-test with local mock pages
  3. Introduce **Feedback Loop**: after clicking, re-scan the page; if red error prompts appear (e.g., "This question is required"), feed error info back to DeepSeek for correction

### Phase 3: Error Handling, Robustness & Concurrency (Week 5)

- **Goal**: Ensure the system can complete multi-page surveys without unexpected interruptions
- **Milestones**:
  1. **Multi-page navigation**: State machine handling "Next" buttons
  2. **Timeout & retry**: Network stalls and LLM response timeouts → automatic non-destructive retry
  3. **Human-in-the-Loop**: On extreme CAPTCHA challenges, pause automation and notify via webhook for manual resolution; resume automatically after

### Phase 4: Deployment, Optimization & Delivery (Week 6)

- **Goal**: Go live and reduce operational cost
- **Milestones**:
  1. **Token optimization**: Trim context sent to DeepSeek, filter irrelevant CSS and useless DOM nodes — ~80% token savings vs raw HTML
  2. **Logging & audit trail**: Every click, every page screenshot, and every DeepSeek reasoning step archived locally for debugging

---

## 4. Architecture Pseudocode

```python
import asyncio
from playwright.async_api import async_playwright
from openai import OpenAI

class SurveyAgent:
    def __init__(self, api_key: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = "deepseek-v4-pro"

    async def inject_and_extract_layout(self, page) -> str:
        """Core spatial transformation: inject JS to tag elements and extract clean structured text"""
        js_script = """
        () => {
            let interactiveElements = document.querySelectorAll('input, button, select, [role="checkbox"]');
            let layoutInfo = [];
            interactiveElements.forEach((el, index) => {
                let id = `ui-id-${index}`;
                el.setAttribute('data-ui-id', id);
                let rect = el.getBoundingClientRect();
                layoutInfo.push({
                    ui_id: id,
                    tag: el.tagName,
                    type: el.type || '',
                    text: el.innerText || el.placeholder || '',
                    visible_position: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                });
            });
            return JSON.stringify({ body_text: document.body.innerText, interactive_elements: layoutInfo });
        }
        """
        return await page.evaluate(js_script)

    def get_decision_from_deepseek(self, page_context: str, filling_requirements: str) -> dict:
        """Brain decision-making"""
        system_prompt = "You are a survey filling expert. Analyze the page structure and make decisions based on requirements. Output must be strict JSON format."
        user_prompt = f"Page layout & elements:\n{page_context}\n\nFilling requirements:\n{filling_requirements}\n\nOutput the next action JSON."
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        return eval(response.choices[0].message.content)

    async def execute_action(self, page, action: dict):
        """Execution layer: precise positioning"""
        ui_id = action.get("ui_id")
        action_type = action.get("type")  # 'click', 'fill', 'scroll'
        
        if action_type == "click":
            await page.click(f'[data-ui-id="{ui_id}"]')
        elif action_type == "fill":
            await page.fill(f'[data-ui-id="{ui_id}"]', action.get("value", ""))
        await asyncio.sleep(0.5)  # Robustness delay

    async def run(self, url: str, requirements: str):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            await page.goto(url)
            await page.wait_for_load_state("networkidle")

            while True:
                # 1. Digitally perceive the page
                layout_data = await self.inject_and_extract_layout(page)
                # 2. Brain reasoning
                decision = self.get_decision_from_deepseek(layout_data, requirements)
                
                print(f"Thought: {decision.get('thought')}")
                if decision.get("status") == "FINISHED":
                    break
                
                # 3. Execute actions
                for action in decision.get("actions", []):
                    await self.execute_action(page, action)
            
            await browser.close()

# agent = SurveyAgent(api_key="your-key", base_url="https://api.deepseek.com/v1")
# asyncio.run(agent.run("https://example.com/survey", "Select agree for all, fill 'N/A' for text"))
```

---

## 5. Why This Architecture Is Highly Maintainable

1. **High UI-change immunity**: Traditional CV approaches break when button colors or skins change; this approach works as long as the HTML nature of the button (e.g., `<input>` tag) remains unchanged — the AI recognizes and clicks it with 100% accuracy.

2. **Minimal dependencies**: No complex agent frameworks (LangChain, AutoGen). Core logic is contained within `inject_and_extract_layout` and the LLM Prompt. A new team member can understand the entire codebase within a day.

3. **Exceptional debuggability**: Playwright provides powerful `trace` and screenshot capabilities. If a step goes wrong, check the locally archived Page Layout JSON and logs — you can immediately tell whether the LLM Prompt needs adjustment or the frontend JS missed an element. Bug localization takes minutes.
