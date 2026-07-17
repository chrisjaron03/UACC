# Security Policy

## ⚠️ Important: UACC Controls Real Input Devices

UACC directly controls your **mouse and keyboard**. This is by design — it's how AI agents interact with desktop applications. However, this power comes with inherent security considerations.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | ✅ Yes             |

## Built-in Safety Features

UACC includes several layers of protection:

1. **Safe Mode** (enabled by default)
   - Blocks actions containing destructive patterns: `delete`, `remove`, `format`, `rm -rf`, `del /f`, `shutdown`, `reboot`
   - Set `UACC_SAFE_MODE=true` in your `.env` file

2. **pyautogui Failsafe**
   - Move your mouse to any screen corner to instantly abort all automation
   - This is a hard stop that cannot be overridden

3. **Action Logging**
   - All actions are logged with timestamps, coordinates, and reasoning
   - Session history is maintained for audit trails

4. **MCP Server Scope**
   - The MCP server only exposes UI interaction tools
   - No file system access, no shell commands, no network requests
   - Actions are limited to mouse, keyboard, and screen reading

## Security Best Practices

When using UACC, we recommend:

- **Never run UACC with elevated privileges** (admin/root) unless absolutely necessary
- **Keep Safe Mode enabled** in production/automated environments
- **Review agent tasks** before execution — know what you're asking the AI to do
- **Use the MCP server** over the standalone agent for better control boundaries
- **Bind SSE/HTTP transports to localhost** (`127.0.0.1`) — never expose to the public internet without authentication
- **Monitor action logs** for unexpected behavior

## Reporting a Vulnerability

If you discover a security vulnerability in UACC, please report it responsibly:

1. **Do NOT open a public GitHub issue** for security vulnerabilities
2. **Email:** Send details to the maintainers via GitHub's private vulnerability reporting feature
3. **Include:**
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### Response Timeline

- **Acknowledgment:** Within 48 hours
- **Assessment:** Within 1 week
- **Fix:** Dependent on severity, typically within 2 weeks for critical issues

### Severity Classification

| Severity | Description | Example |
|----------|-------------|---------|
| **Critical** | Remote code execution or privilege escalation | MCP server allows arbitrary command execution |
| **High** | Safety bypass or data exposure | Safe mode can be circumvented |
| **Medium** | Unexpected behavior that could cause harm | Action coordinates systematically wrong |
| **Low** | Minor issues with limited impact | Logging exposes sensitive text from screen |

## Scope

This security policy covers:
- The `uacc` Python package
- The `uacc_mcp` MCP server
- Example scripts in `examples/`
- Configuration handling (`.env` file parsing)

This policy does **not** cover:
- Third-party dependencies (EasyOCR, pyautogui, etc.) — report those upstream
- LLM provider APIs (OpenAI, Anthropic, etc.)
- The user's system configuration or security posture
