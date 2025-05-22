import sys
import os
import toml
import html # For escaping

# Add project root to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    from utils.static import DEFAULT_CONFIG_TOML
except ImportError:
    print("Error: Could not import DEFAULT_CONFIG_TOML from utils.static.")
    print("Ensure the script is run from the project root or the path is correctly set.")
    sys.exit(1)

# --- Comment and Structure Parsing Logic ---
def parse_toml_structure_with_comments(toml_string: str) -> dict:
    """
    Parses a TOML string to extract keys, default values, types, and comments.
    Returns a structured dictionary representing the TOML content.
    """
    parsed_toml = toml.loads(toml_string) # For structure and default values
    config_spec = {} # Will store {path: {'value': ..., 'comment': ..., 'type': ...}}
    
    lines = toml_string.splitlines()
    current_path_parts = []
    active_comment_block = []

    for line in lines:
        stripped_line = line.strip()

        if stripped_line.startswith('#'):
            active_comment_block.append(stripped_line[1:].strip())
        elif stripped_line.startswith('['):
            # Section header
            section_name_full = stripped_line[1:-1].strip()
            
            # Handle array of tables [[array.of.tables]] vs simple table [table]
            is_array_of_tables = section_name_full.startswith('[') and section_name_full.endswith(']')
            if is_array_of_tables:
                section_name_full = section_name_full[1:-1] # remove outer [] for path

            current_path_parts = section_name_full.split('.')
            
            # For sections, comments apply to the section itself.
            # We'll store section comments with a special key like 'section_path.__comment__'
            # Or, associate with the first key in that section if a section-specific comment display is too complex.
            # For now, comments before keys are associated with those keys.
            # Comments directly under a section header, before any keys, could be section comments.
            # This simplistic parser will associate comments with the *next* available key or sub-section.
            # A more advanced parser would assign them to the section directly.
            
        elif '=' in stripped_line:
            # Key-value pair
            key, value_str = stripped_line.split('=', 1)
            key = key.strip()
            
            full_key_path = '.'.join(current_path_parts + [key])
            
            # Get default value and type from pre-parsed TOML
            # This is safer for complex types than re-parsing value_str
            current_level_parsed = parsed_toml
            for part in current_path_parts:
                current_level_parsed = current_level_parsed.get(part, {})
            default_value = current_level_parsed.get(key)

            item_type = type(default_value).__name__
            if isinstance(default_value, list) and default_value:
                # More specific type for lists, e.g. "list_of_str", "list_of_int"
                item_type = f"list_of_{type(default_value[0]).__name__}"
            elif isinstance(default_value, dict): # This is a sub-table defined inline
                item_type = "table"


            config_spec[full_key_path] = {
                'value': default_value,
                'comment': '\n'.join(active_comment_block).strip(),
                'type': item_type,
                'path_parts': list(current_path_parts + [key]) # Store path parts for JS
            }
            active_comment_block = []
        elif not stripped_line: # Blank line
            # Reset comments if a blank line is encountered before a key/section
            active_comment_block = []
        # Array of tables elements or other complex structures might need more handling
        # if not handled by the initial `toml.loads()` for default values.

    return parsed_toml, config_spec


# --- HTML Generation Logic ---

def to_title_case(snake_str: str) -> str:
    return ' '.join(word.capitalize() for word in snake_str.split('_'))

def generate_html_input(key_path: str, item_spec: dict, current_config_level: dict) -> str:
    """Generates HTML for a single config item."""
    label = to_title_case(item_spec['path_parts'][-1])
    comment = html.escape(item_spec['comment'])
    value = item_spec['value']
    item_type = item_spec['type']
    
    # Use key_path for ID to ensure uniqueness
    # JS will need to reconstruct nested structure from these dot-separated paths
    input_id = key_path 

    html_parts = [f'<div class="mb-3" data-config-path="{key_path}">']
    html_parts.append(f'<label for="{input_id}" class="form-label">{label}</label>')
    
    if comment:
        html_parts.append(f'<p class="form-text text-muted">{comment}</p>')

    if item_type == 'bool':
        html_parts.append(f'<select class="form-select" id="{input_id}">')
        html_parts.append(f'<option value="true"{" selected" if value else ""}>是 (Yes)</option>')
        html_parts.append(f'<option value="false"{" selected" if not value else ""}>否 (No)</option>')
        html_parts.append('</select>')
    elif item_type == 'int' or item_type == 'float':
        step = "0.01" if item_type == 'float' else "1"
        html_parts.append(f'<input type="number" class="form-control" id="{input_id}" value="{value}" step="{step}">')
    elif item_type == 'str':
        # Check for multiline potential (e.g. if default value has newline or is long)
        if isinstance(value, str) and ('\n' in value or len(value) > 80):
             html_parts.append(f'<textarea class="form-control" id="{input_id}" rows="3">{html.escape(value)}</textarea>')
        else:
             html_parts.append(f'<input type="text" class="form-control" id="{input_id}" value="{html.escape(value)}">')
    elif item_type.startswith('list_of_'):
        # For lists, use textarea, one item per line
        list_items_str = "\n".join(map(str, value if value else []))
        html_parts.append(f'<textarea class="form-control" id="{input_id}" rows="3" placeholder="每行一个值">{html.escape(list_items_str)}</textarea>')
    else: # Default for unknown or complex types (like nested tables handled by recursion)
        html_parts.append(f'<input type="text" class="form-control" id="{input_id}" value="{html.escape(str(value))}" title="Type: {item_type}">')
        html_parts.append(f'<p class="form-text text-muted small">Unhandled type: {item_type}. Displayed as text.</p>')

    html_parts.append('</div>')
    return "\n".join(html_parts)

def generate_html_for_level(parsed_level: dict, spec: dict, path_prefix: list, accordion_parent_id: str, level: int = 0) -> str:
    """Recursively generates HTML for a level of the config."""
    html_content = []
    
    # Sort keys: tables first, then others
    sorted_keys = sorted(parsed_level.keys(), key=lambda k: not isinstance(parsed_level[k], dict))

    for key in sorted_keys:
        current_full_path_parts = path_prefix + [key]
        current_full_path_str = '.'.join(current_full_path_parts)
        
        item_value = parsed_level[key]
        item_spec = spec.get(current_full_path_str)

        if isinstance(item_value, dict) and not (item_spec and item_spec['type'] == 'table_inline_empty'): # It's a table/section
            accordion_id = f"collapse_{current_full_path_str.replace('.', '_')}"
            heading_id = f"heading_{current_full_path_str.replace('.', '_')}"
            
            section_comment_key = f"{current_full_path_str}.__section_comment__" # Hypothetical
            section_comment = spec.get(section_comment_key, {}).get('comment', '') # Try to get section specific comment

            html_content.append('<div class="accordion-item">')
            html_content.append(f'<h2 class="accordion-header" id="{heading_id}">')
            html_content.append(
                f'<button class="accordion-button {"" if level == 0 else "collapsed"}" type="button" data-bs-toggle="collapse" '
                f'data-bs-target="#{accordion_id}" aria-expanded="{"true" if level == 0 else "false"}" aria-controls="{accordion_id}">'
                f'{to_title_case(key)}'
                '</button>'
            )
            html_content.append('</h2>')
            html_content.append(
                f'<div id="{accordion_id}" class="accordion-collapse collapse {"show" if level == 0 else ""}" '
                f'aria-labelledby="{heading_id}" data-bs-parent="#{accordion_parent_id}">'
            )
            html_content.append('<div class="accordion-body">')
            if section_comment:
                 html_content.append(f'<p class="form-text text-muted">{html.escape(section_comment)}</p>')

            # Recursive call for nested tables
            html_content.append(generate_html_for_level(item_value, spec, current_full_path_parts, accordion_id, level + 1))
            
            html_content.append('</div></div></div>') # accordion-body, accordion-collapse, accordion-item
        elif item_spec: # It's a key-value pair that has a spec
            html_content.append(generate_html_input(current_full_path_str, item_spec, parsed_level))
        # Else: key from parsed_toml not found in spec (e.g. if spec parsing is incomplete, or it's an empty table)
        # or it's an array of tables - this basic generator doesn't fully support arrays of tables for editing.

    return "\n".join(html_content)


# --- Main Script Logic ---
def main():
    parsed_structure, item_specs = parse_toml_structure_with_comments(DEFAULT_CONFIG_TOML)

    # Generate form HTML from parsed_structure and item_specs
    form_html = generate_html_for_level(parsed_structure, item_specs, [], "configAccordion")

    # HTML Template
    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Steamauto 配置文件生成器 (TOML)</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ font-family: "微软雅黑", "Helvetica Neue", Arial, sans-serif; display: flex; flex-direction: column; min-height: 100vh; }}
        pre {{ white-space: pre-wrap; word-wrap: break-word; }}
        .logo {{ height: 50px; margin-right: 10px; }}
        .container {{ flex: 1 0 auto; padding-bottom: 0.1rem; }}
        .footer {{ flex-shrink: 0; margin-top: auto; background-color: #343a40; color: white; }}
        .footer a {{ color: #ffffff; transition: color 0.3s ease; text-decoration: none; }}
        .footer a:hover {{ color: #17a2b8; }}
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="https://github.com/Steamauto/Steamauto" target="_blank">
                <img src="https://camo.githubusercontent.com/62564b6ae17c50f60303e79d606094dda9e3cab1df9243f091c8756b98ebbb8a/68747470733a2f2f736f6369616c6966792e6769742e63692f6a69616a696178642f537465616d6175746f2f696d6167653f6465736372697074696f6e3d31266c616e67756167653d31266e616d653d31266f776e65723d31267468656d653d4c69676874"
                    alt="Steamauto Logo" class="logo">
                Steamauto 配置文件生成器 (TOML来源)
            </a>
        </div>
    </nav>

    <div class="container mt-5">
        <div class="alert alert-info" role="alert">
            此页面根据 <code>utils/static.py</code> 中的 <code>DEFAULT_CONFIG_TOML</code> 动态生成。
            修改默认值或注释请直接编辑该Python文件中的字符串，然后重新运行此脚本。
        </div>
        <div class="accordion" id="configAccordion">
            {form_html}
        </div>

        <div class="d-grid gap-2 mt-5">
            <button class="btn btn-primary btn-lg" type="button" id="generateConfig">生成配置文件内容 (JSON5)</button>
        </div>

        <div class="mt-5">
            <h3>生成的 config.json5 文件内容 (供复制或下载):</h3>
            <pre id="configOutput" class="bg-light p-3"></pre>
            <button class="btn btn-success mt-2" id="copyConfig">复制到剪贴板</button>
            <button class="btn btn-secondary mt-2" id="downloadConfig">下载 config.json5 文件</button>
        </div>
        <div class="mb-5"></div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/js/bootstrap.bundle.min.js"></script>
    <script>
        // Helper to set nested properties in an object
        function setNestedValue(obj, path, value) {{
            const parts = path.split('.');
            let current = obj;
            for (let i = 0; i < parts.length - 1; i++) {{
                current = current[parts[i]] = current[parts[i]] || {{}};
            }}
            current[parts[parts.length - 1]] = value;
        }}

        document.getElementById('generateConfig').addEventListener('click', () => {{
            const config = {{}};
            const formElements = document.querySelectorAll('[data-config-path]');
            
            formElements.forEach(el => {{
                const path = el.getAttribute('data-config-path');
                const inputElement = el.querySelector('input, select, textarea');
                let value = inputElement.value;

                // Type conversion based on original type or input type
                const originalSpec = {item_specs.get("'+path+'", {{}})} // This needs dynamic injection or JS access to spec
                // For simplicity, infer from input type or known bools/numbers from IDs
                
                if (inputElement.tagName === 'SELECT' && (inputElement.id === path)) {{ // Boolean select
                    value = (value === 'true');
                }} else if (inputElement.type === 'number') {{
                    value = parseFloat(inputElement.value);
                    if (isNaN(value)) value = inputElement.value; // Keep as string if not a valid number
                }} else if (inputElement.tagName === 'TEXTAREA' && inputElement.id === path) {{
                    // Assume list of strings for textareas that are not general text
                    // This needs refinement: check original type from spec if possible
                    // For now, a simple heuristic: if it looks like a list from DEFAULT_CONFIG_TOML
                    const isListField = {str([k for k, v in item_specs.items() if v['type'].startswith('list_of_')])};
                    if(isListField.includes(path)) {{
                        value = inputElement.value.split('\\n').map(s => s.trim()).filter(s => s !== "");
                        // Try to convert to numbers if list_of_int/float
                        if (inputElement.id.includes("time") && path.includes("time")) { // Heuristic for hour lists
                             value = value.map(s => parseInt(s)).filter(n => !isNaN(n));
                        }
                    }}
                }}
                // Add more type conversions as needed, e.g. for integer lists

                setNestedValue(config, path, value);
            }});

            // Temporary: Forcing some known list fields to be arrays if textarea logic is not perfect
            // This section should be ideally driven by the spec directly in JS
            if(config.notify_service && config.notify_service.notifiers === "") config.notify_service.notifiers = [];
            if(config.notify_service && config.notify_service.blacklist_words === "") config.notify_service.blacklist_words = [];
            // ... Add similar empty string to empty list conversions for all list fields ...
            // Example for buff_auto_on_sale.blacklist_time
            let pathsToEnsureArray = {str([k for k, v in item_specs.items() if v['type'].startswith('list_of_')])};
            pathsToEnsureArray.forEach(p => {{
                let current = config;
                const parts = p.split('.');
                for(let i=0; i<parts.length-1; ++i) {{ current = current[parts[i]]; if(!current) break; }}
                if(current && current[parts[parts.length-1]] === "") {{
                    current[parts[parts.length-1]] = [];
                }}
            }});


            const configString = JSON.stringify(config, null, 2); // Outputting JSON5 compatible
            document.getElementById('configOutput').textContent = configString;
        }});

        document.getElementById('copyConfig').addEventListener('click', () => {{
            navigator.clipboard.writeText(document.getElementById('configOutput').textContent)
                .then(() => alert('配置内容已复制到剪贴板！'))
                .catch(err => alert('复制失败: ' + err));
        }});

        document.getElementById('downloadConfig').addEventListener('click', () => {{
            const configText = document.getElementById('configOutput').textContent;
            const blob = new Blob([configText], {{ type: 'application/json;charset=utf-8' }}); // Changed type
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'config.json5'; // Still download as json5 for user convenience
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }});
    </script>
</body>
<footer class="footer mt-auto py-3 bg-dark text-white">
    <div class="container text-center">
        <a href="https://github.com/Steamauto/Steamauto" target="_blank">
            Edit on GitHub / View Source of this Page
        </a>
    </div>
</footer>
</html>"""

    output_path = os.path.join(project_root, "pages", "config.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_template)
    
    print(f"Successfully generated {output_path}")
    print(f"Item specs parsed: {len(item_specs)}")
    # For debugging, print a snippet of item_specs
    # for k, v in list(item_specs.items())[:5]:
    #    print(f"{k}: {v}")


if __name__ == "__main__":
    main()

```
