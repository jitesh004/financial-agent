import os
import textwrap

BASE_DIR = r"d:\python\financial-agent\frontend\src\components"

files = {
    "DataManager.jsx": """\
import React from 'react';
export default function DataManager() { return <div>DataManager</div>; }
""",
    "WorkflowNav.jsx": """\
import React from 'react';
export default function WorkflowNav() { return <div>WorkflowNav</div>; }
""",
    "ReviewQueue.jsx": """\
import React from 'react';
export default function ReviewQueue() { return <div>ReviewQueue</div>; }
""",
    "Claims.jsx": """\
import React from 'react';
export default function Claims() { return <div>Claims</div>; }
""",
    "Recurring.jsx": """\
import React from 'react';
export default function Recurring() { return <div>Recurring</div>; }
"""
}

for name, content in files.items():
    with open(os.path.join(BASE_DIR, name), "w", encoding="utf-8") as f:
        f.write(content)

print("Files created.")
