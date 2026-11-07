# FloBar project instructions

- Keep user-facing replies concise, direct, and non-repetitive. Skip routine narration; give one next action when user input is needed.
- Inspect existing project notes and prior verification before repeating discovery or checks. Re-run a check only when code/configuration changed or its earlier result is uncertain.
- For AI-powered drafts, summaries, code suggestions, edits, and testing assistance, use only the local Ollama server at http://127.0.0.1:11434 with model qwen2.5-coder:14b. Treat model output as unverified and review it before relying on it. Do not route project content to cloud models or external AI APIs without explicit user approval.
- Preserve FloBar's existing architecture, stability, and safety constraints. Do not install dependencies, extensions, tools, or services, or broaden system/app permissions, without the user's approval.
- Work only within `/Users/galtalor/Documents/chatGPT/FloBar` unless the user explicitly authorizes another path. Never claim this folder boundary is enforced by VS Code or macOS unless verified.
- For Continue, use only the configured local Ollama model (qwen2.5-coder:14b at http://127.0.0.1:11434). Keep Agent tools in ask-before-use mode; never make file edits or terminal commands automatic. Show the proposed change and wait for approval before applying each code change.
