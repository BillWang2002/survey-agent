"""
Core JS injection module — assigns unique data-ui-id attributes to all
interactive elements on the page and extracts structured spatial layout info.

This is the "Perception Layer" that bridges the visual DOM to structured text,
enabling the LLM to understand page layout without pixel-level CV analysis.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# JavaScript injection scripts
# ---------------------------------------------------------------------------

# Primary injection script: tags elements and extracts layout
INJECT_AND_EXTRACT_SCRIPT = """
() => {
    // Selectors for all interactive and survey-relevant elements
    const INTERACTIVE_SELECTORS = [
        'input',
        'button',
        'select',
        'textarea',
        'a[href]',
        '[role="button"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="slider"]',
        '[role="combobox"]',
        '[role="listbox"]',
        '[role="tab"]',
        '[role="switch"]',
        '[contenteditable="true"]',
        '.clickable',
        '[data-survey-option]',
    ];

    const result = {
        interactive_elements: [],
        body_text: '',
        page_title: document.title || '',
        page_url: window.location.href,
    };

    let uidCounter = 0;

    // Collect & tag elements matching our selectors
    const selectorString = INTERACTIVE_SELECTORS.join(', ');
    const elements = document.querySelectorAll(selectorString);

    elements.forEach((el) => {
        const uid = `ui-id-${uidCounter}`;
        uidCounter++;

        // Tag the element for later Playwright targeting
        el.setAttribute('data-ui-id', uid);

        const tag = el.tagName.toLowerCase();
        const inputType = el.getAttribute('type') || '';
        const isRadioOrCheckbox = (tag === 'input' && (inputType === 'radio' || inputType === 'checkbox'));

        let rect = el.getBoundingClientRect();
        let isVisible = (
            rect.width > 0 &&
            rect.height > 0 &&
            rect.x >= 0 &&
            rect.y >= 0
        );

        // For hidden radio/checkbox inputs (common survey platform pattern:
        // the native input is hidden by CSS, and the clickable label is styled
        // to look like a radio button). Use the label's position data so the
        // element appears in the interactive elements table.
        let clickTargetEl = el;
        let usedLabelRect = false;
        if (!isVisible && isRadioOrCheckbox) {
            // Try parent <label> wrapper first (most common pattern)
            let visibleLabel = el.closest('label');
            // Also try <label for="..."> sibling (label next to input)
            if (!visibleLabel && el.getAttribute('id')) {
                visibleLabel = document.querySelector(`label[for="${el.getAttribute('id')}"]`);
            }
            if (visibleLabel) {
                const labelRect = visibleLabel.getBoundingClientRect();
                if (labelRect.width > 0 && labelRect.height > 0) {
                    rect = labelRect;
                    isVisible = true;
                    clickTargetEl = visibleLabel;
                    usedLabelRect = true;
                    // Also tag the label so both the input and label can be targeted
                    visibleLabel.setAttribute('data-ui-id', uid);
                }
            }
        }

        // Build element descriptor
        const descriptor = {
            ui_id: uid,
            tag: tag,
            type: el.getAttribute('role') || inputType || '',
            name: el.getAttribute('name') || el.getAttribute('aria-label') || '',
            text: (() => {
                // For hidden radio/checkbox inputs, use the visible label text
                // so the LLM sees human-readable option text (e.g. "New South Wales")
                if (usedLabelRect && clickTargetEl !== el) {
                    const labelText = (clickTargetEl.innerText || '').trim();
                    if (labelText) return labelText.substring(0, 200);
                }
                return (el.getAttribute('value')
                    || el.getAttribute('placeholder')
                    || el.getAttribute('aria-label')
                    || el.innerText
                    || el.getAttribute('name')
                    || '').trim().substring(0, 200);
            })(),
            id: el.getAttribute('id') || '',
            class: el.getAttribute('class') || '',
            href: el.getAttribute('href') || '',
            checked: el.checked || false,
            disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
            required: el.required || el.getAttribute('aria-required') === 'true',
            visible_position: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
            is_visible: isVisible,
            computed_styles: {
                display: window.getComputedStyle(el).display,
                visibility: window.getComputedStyle(el).visibility,
                opacity: window.getComputedStyle(el).opacity,
            },
            // Capture nearby label text for context
            label_text: findLabelText(el),
            // For table/matrix structures: row & column context
            table_context: findTableContext(el),
        };

        result.interactive_elements.push(descriptor);
    });

    // Extract full body visible text for context
    if (document.body) {
        result.body_text = (document.body.innerText || '').trim().substring(0, 6000);
    }

    return JSON.stringify(result);

    // --- Helper functions ---

    function findLabelText(el) {
        // Check aria-labelledby
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const labelEl = document.getElementById(labelledBy);
            if (labelEl) return labelEl.innerText.trim().substring(0, 200);
        }
        // Check associated <label>
        const elId = el.getAttribute('id');
        if (elId) {
            const label = document.querySelector(`label[for="${elId}"]`);
            if (label) return label.innerText.trim().substring(0, 200);
        }
        // Check parent <label>
        const parentLabel = el.closest('label');
        if (parentLabel) {
            const fullText = parentLabel.innerText.trim();
            // Remove the input's own text to get just the label part
            const elText = el.innerText || '';
            if (fullText.endsWith(elText)) {
                return fullText.substring(0, fullText.length - elText.length).trim().substring(0, 200);
            }
            return fullText.substring(0, 200);
        }
        // Check preceding sibling text
        if (el.previousSibling && el.previousSibling.textContent) {
            return el.previousSibling.textContent.trim().substring(0, 200);
        }
        return '';
    }

    function findTableContext(el) {
        const td = el.closest('td, th');
        if (!td) return null;
        const tr = td.closest('tr');
        const table = tr ? tr.closest('table, [role="grid"]') : null;
        if (!tr || !table) return null;

        const cells = Array.from(tr.querySelectorAll('td, th'));
        const colIndex = cells.indexOf(td);

        // Get column header
        let colHeader = '';
        if (table) {
            const headers = table.querySelectorAll('thead th, [role="columnheader"]');
            if (headers[colIndex]) {
                colHeader = headers[colIndex].innerText.trim().substring(0, 100);
            }
        }

        // Get row header (first cell in the row)
        let rowHeader = '';
        if (cells.length > 0 && cells[0] !== td) {
            rowHeader = cells[0].innerText.trim().substring(0, 100);
        }

        return {
            row_header: rowHeader,
            col_header: colHeader,
            col_index: colIndex,
            total_cols: cells.length,
        };
    }
}
"""

# Simplified script: only tags elements with data-ui-id (no extraction)
TAG_ONLY_SCRIPT = """
() => {
    const SELECTORS = 'input, button, select, textarea, [role="button"], [role="checkbox"], [role="radio"], [role="slider"], [role="combobox"], [role="tab"], [role="switch"]';
    const elements = document.querySelectorAll(SELECTORS);
    elements.forEach((el, i) => {
        el.setAttribute('data-ui-id', `ui-id-${i}`);
    });
    return elements.length;
}
"""

# Script to detect validation errors after form submission
DETECT_ERRORS_SCRIPT = """
() => {
    const errors = [];

    // Common error selectors across survey platforms
    const errorSelectors = [
        '.error',
        '.error-message',
        '.validation-error',
        '.field-error',
        '[role="alert"]',
        '.form-error',
        '.has-error .help-block',
        '.invalid-feedback',
        '.survey-error',
        '[data-error]',
        '.wj-error',  // WJC survey platform
        '.err-msg',   // Common Chinese survey platforms
        '.error-tip',
    ];

    errorSelectors.forEach(selector => {
        document.querySelectorAll(selector).forEach(el => {
            const text = el.innerText.trim();
            if (text && el.offsetParent !== null) {  // visible
                errors.push({
                    selector: selector,
                    text: text.substring(0, 300),
                    // Try to find the associated field
                    field_name: findAssociatedField(el),
                });
            }
        });
    });

    // Also check for HTML5 constraint validation messages
    document.querySelectorAll('input:invalid, select:invalid, textarea:invalid').forEach(el => {
        errors.push({
            selector: el.tagName,
            text: el.validationMessage || 'Field is invalid',
            field_name: el.getAttribute('name') || el.getAttribute('id') || '',
            ui_id: el.getAttribute('data-ui-id') || '',
        });
    });

    return JSON.stringify({ has_errors: errors.length > 0, errors: errors });

    function findAssociatedField(el) {
        const container = el.closest('.form-group, .question, .field, [data-question], .question-row, tr');
        if (container) {
            const label = container.querySelector('label, .question-text, .q-title, .field-label');
            if (label) return label.innerText.trim().substring(0, 200);
        }
        return '';
    }
}
"""

# Script to detect human-verification / CAPTCHA challenges on the page
DETECT_CAPTCHA_SCRIPT = """
() => {
    const findings = [];

    // ---- Category 1: Known CAPTCHA iframes ----
    //
    // IMPORTANT: Two-level detection to avoid false blocking.
    //
    // Level A — BLOCKING: Active challenge iframes (image selection popup,
    // slider, puzzle). Only visible when the user genuinely needs to solve
    // a challenge. These trigger has_captcha = true.
    const blockingIframeSelectors = [
        // Google reCAPTCHA challenge iframe (image selection popup)
        'iframe[src*="recaptcha/api2/bframe"]',
        // hCaptcha challenge iframe
        'iframe[src*="hcaptcha.com/captcha"]',
        // Cloudflare Turnstile challenge
        'iframe[src*="challenges.cloudflare.com"]',
        // Chinese CAPTCHA platform iframes (always require interaction)
        'iframe[src*="geetest.com"]',
        'iframe[src*="yidun.com"]',
        'iframe[src*="netease.com/yidun"]',
        'iframe[src*="dingxiang"]',
        'iframe[src*="shumei"]',
        // Generic "captcha" in URL — exclude reCAPTCHA / hCaptcha which are
        // handled by their own specific selectors above & below.
        // ":not([src*=\"recaptcha\"])" prevents matching recaptcha anchors
        // since "recaptcha" contains the substring "captcha".
        'iframe[src*="captcha"]:not([src*="recaptcha"]):not([src*="hcaptcha"])',
    ];

    blockingIframeSelectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                findings.push({
                    type: 'captcha_challenge',
                    subtype: el.getAttribute('src') || '',
                    element: 'iframe',
                    text: 'Active CAPTCHA challenge iframe detected',
                });
            }
        });
    });

    // Level B — INFORMATIONAL: CAPTCHA service indicators (reCAPTCHA badge,
    // checkbox anchor, Turnstile widget). These are ALWAYS present on pages
    // that use a CAPTCHA service — they do NOT indicate an active challenge.
    // Log them for visibility but do NOT block automation.
    const indicatorIframeSelectors = [
        // Google reCAPTCHA anchor (checkbox / v3 badge)
        'iframe[src*="recaptcha/api2/anchor"]',
        'iframe[src*="recaptcha.net"]',
        'iframe[src*="google.com/recaptcha"]',
        'iframe[src*="recaptcha/api"]',
        // hCaptcha checkbox
        'iframe[src*="hcaptcha.com"]',
        'iframe[src*="hcaptcha/"]',
    ];
    const indicatorFindings = [];
    const indicatorElements = new Set();
    indicatorIframeSelectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                indicatorElements.add(el);
                indicatorFindings.push({
                    type: 'captcha_indicator',
                    subtype: el.getAttribute('src') || '',
                    element: 'iframe',
                    text: 'CAPTCHA service indicator (badge/checkbox — not an active challenge)',
                });
            }
        });
    });

    // Title-based detection. Skip iframes already flagged as indicators —
    // their title attributes (e.g. title="reCAPTCHA") contain "captcha"
    // but they do NOT represent an active challenge.
    const titleIframeSelectors = [
        'iframe[title*="captcha" i]',
        'iframe[title*="recaptcha" i]',
        'iframe[title*="challenge" i]',
    ];
    titleIframeSelectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null && !indicatorElements.has(el)) {
                findings.push({
                    type: 'captcha_challenge',
                    subtype: el.getAttribute('src') || el.getAttribute('title') || '',
                    element: 'iframe',
                    text: 'CAPTCHA iframe with matching title detected',
                });
            }
        });
    });

    // ---- Category 2: CAPTCHA containers ----
    //
    // Two-level: service widgets (indicators) vs active challenge containers.
    //
    // Level A — BLOCKING: Active challenge containers (image CAPTCHAs,
    // slider puzzles, verification code inputs visible to the user).
    const blockingContainers = [
        // Chinese CAPTCHA platforms — active challenge widgets
        '.geetest_captcha',
        '.geetest_panel',
        '.yidun_captcha',
        '.yidun_slider',
        '.captcha-img',
        '.verify-img',
        '.img-captcha',
        '.sms-captcha',
        '#imgCode',
        '#smsCode',
        '[class*="captcha-box"]',
        '[class*="verify-code"]',
        '[class*="image-code"]',
        '[id*="captchaImg"]',
        '[id*="verifyImg"]',
    ];
    blockingContainers.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                findings.push({
                    type: 'captcha_container',
                    subtype: sel,
                    element: el.tagName + (el.className ? '.' + el.className.split(' ')[0] : ''),
                    text: (el.innerText || '').trim().substring(0, 80),
                });
            }
        });
    });

    // Level B — INFORMATIONAL: CAPTCHA service widgets (reCAPTCHA v2/v3,
    // hCaptcha, Turnstile). These containers are always present on pages
    // that use the service — they do NOT indicate an active challenge.
    const indicatorContainers = [
        '.g-recaptcha',
        '.h-captcha',
        '[data-sitekey]',
        '#recaptcha',
        '#hcaptcha',
        '.cf-turnstile',
        '[data-callback]',
    ];
    indicatorContainers.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                indicatorFindings.push({
                    type: 'captcha_indicator',
                    subtype: sel + (el.getAttribute('data-sitekey') ? ' sitekey=' + el.getAttribute('data-sitekey').substring(0, 16) : ''),
                    element: el.tagName + (el.className ? '.' + el.className.split(' ')[0] : ''),
                    text: 'CAPTCHA service widget (badge/checkbox — not an active challenge)',
                });
            }
        });
    });

    // ---- Category 3: Slider / drag verification (Chinese platforms) ----
    const sliderPatterns = [
        '.slider-verify',
        '.slide-verify',
        '.drag-verify',
        '.slider-captcha',
        '.nc_wrapper',
        '.captcha-slider',
        '[class*="slide-verify"]',
        '[class*="slider-captcha"]',
        '.yidun_slider',
    ];
    sliderPatterns.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                findings.push({
                    type: 'slider_captcha',
                    subtype: sel,
                    element: el.tagName,
                    text: (el.innerText || el.getAttribute('aria-label') || 'Slider CAPTCHA').substring(0, 80),
                });
            }
        });
    });

    // ---- Category 4: Text-based verification indicators ----
    const textPatterns = [
        "I'm not a robot",
        'Verify you are human',
        'Please verify',
        'Security check',
        'Click the images',
        'Select all images',
        'Prove you are human',
        'Complete the security check',
        'One more step',
        'Please prove you are human',
    ];
    const bodyText = (document.body ? document.body.innerText : '') || '';
    textPatterns.forEach(pattern => {
        if (bodyText.includes(pattern)) {
            findings.push({
                type: 'text_captcha',
                subtype: pattern,
                element: 'body',
                text: pattern,
            });
        }
    });

    // ---- Category 5: Suspicious overlays / modals ----
    const overlaySelectors = [
        '.captcha-modal',
        '.verify-modal',
        '.challenge-modal',
        '[class*="captcha-overlay"]',
        '[class*="verify-overlay"]',
        '[id*="captcha-modal"]',
        '[class*="verification"]',
    ];
    overlaySelectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.offsetParent !== null) {
                findings.push({
                    type: 'captcha_overlay',
                    subtype: sel,
                    element: el.tagName,
                    text: (el.innerText || '').trim().substring(0, 80),
                });
            }
        });
    });

    // ---- Merge indicator findings into the main list (for logging) ----
    indicatorFindings.forEach(f => findings.push(f));

    // ---- Deduplicate ----
    const seen = new Set();
    const unique = [];
    findings.forEach(f => {
        const key = f.type + '|' + f.subtype + '|' + f.text;
        if (!seen.has(key)) {
            seen.add(key);
            unique.push(f);
        }
    });

    // Only blocking types count towards has_captcha.
    // captcha_indicator = reCAPTCHA badge/checkbox always present on pages
    // that use a CAPTCHA service — it does NOT mean a challenge is active.
    const blockingTypes = ['captcha_challenge', 'captcha_container', 'slider_captcha', 'text_captcha', 'captcha_overlay'];
    const blockingFindings = unique.filter(f => blockingTypes.includes(f.type));

    return JSON.stringify({
        has_captcha: blockingFindings.length > 0,
        captcha_count: unique.length,
        blocking_count: blockingFindings.length,
        captchas: unique,
    });
}
"""

# Script to check if a previously detected CAPTCHA has been resolved
CHECK_CAPTCHA_RESOLVED_SCRIPT = """
() => {
    const bodyText = (document.body ? document.body.innerText : '') || '';

    const captchaIframes = document.querySelectorAll(
        'iframe[src*="recaptcha/api2/bframe"], iframe[src*="hcaptcha.com/captcha"], iframe[src*="challenges.cloudflare.com"], iframe[src*="geetest.com"], iframe[src*="yidun.com"], iframe[src*="netease.com/yidun"], iframe[src*="dingxiang"], iframe[src*="shumei"], iframe[src*="captcha"]:not([src*="recaptcha"]):not([src*="hcaptcha"])'
    );
    for (const f of captchaIframes) {
        if (f.offsetParent !== null) return false;
    }

    // Only check blocking-level containers (skip .g-recaptcha, [data-sitekey],
    // .cf-turnstile which are always-present service widgets)
    const containers = document.querySelectorAll(
        '.slider-verify, .slide-verify, .drag-verify, .geetest_captcha, .yidun_captcha, .captcha-img, .verify-img, [class*="captcha-box"], [class*="verify-code"]'
    );
    for (const c of containers) {
        if (c.offsetParent !== null) return false;
    }

    const textPatterns = [
        "I'm not a robot", 'Verify you are human',
        'Please verify', 'Security check', 'Complete the security check',
    ];
    for (const p of textPatterns) {
        if (bodyText.includes(p)) return false;
    }

    return true;
}
"""


# ---------------------------------------------------------------------------
# Python helper functions
# ---------------------------------------------------------------------------

def parse_layout_json(raw_json: str) -> dict[str, Any]:
    """
    Parse the raw JSON string returned by the inject script into a Python dict.

    Args:
        raw_json: JSON string from page.evaluate()

    Returns:
        Parsed layout dictionary with interactive_elements and body_text.

    Raises:
        ValueError: If JSON parsing fails.
    """
    try:
        layout = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse layout JSON from page: {e}") from e

    return layout


def build_compact_layout(layout: dict[str, Any]) -> str:
    """
    Convert the raw layout dict into a compact, token-efficient text representation
    suitable for sending to the LLM.

    Args:
        layout: Parsed layout dict from parse_layout_json().

    Returns:
        Compact markdown-like string describing the page.
    """
    lines = []
    lines.append(f"## Page: {layout.get('page_title', 'Unknown')}")
    lines.append(f"URL: {layout.get('page_url', '')}")
    lines.append("")

    elements = layout.get("interactive_elements", [])

    if not elements:
        lines.append("*(No interactive elements found on this page)*")
        return "\n".join(lines)

    # Group elements by tag type for readability
    lines.append(f"### Interactive Elements ({len(elements)} total)")
    lines.append("")

    lines.append("| ui-id | Tag | Type | Text/Label | Position | Disabled |")
    lines.append("|-------|-----|------|------------|----------|----------|")
    for el in elements:
        pos = el.get("visible_position", {})
        pos_str = f"({pos.get('x', 0)}, {pos.get('y', 0)})"
        text = el.get("text", "") or el.get("label_text", "") or el.get("name", "")
        disabled = "❌" if el.get("disabled") else ""
        hidden_marker = " 👻" if not el.get("is_visible", True) else ""
        lines.append(
            f"| `{el['ui_id']}` | {el['tag']}{hidden_marker} | {el.get('type', '')} | "
            f"{text[:60]} | {pos_str} | {disabled} |"
        )

    lines.append("")

    # Add body text summary
    body = layout.get("body_text", "")
    if body:
        lines.append("### Page Text Content")
        lines.append("```text")
        lines.append(body[:3000])  # Truncate to save tokens
        lines.append("```")

    return "\n".join(lines)
