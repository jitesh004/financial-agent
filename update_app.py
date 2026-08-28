import os
import re

app_path = r"d:\python\financial-agent\frontend\src\App.jsx"
with open(app_path, "r", encoding="utf-8") as f:
    content = f.read()

imports_to_add = """
import DataManager from './components/DataManager';
import WorkflowNav from './components/WorkflowNav';
import ReviewQueue from './components/ReviewQueue';
import Claims from './components/Claims';
import Recurring from './components/Recurring';
import MonthView from './components/MonthView';
"""

content = content.replace("import { api } from './lib';", "import { api } from './lib';\n" + imports_to_add)

tabs_new = """const TABS = [
  ['overview', 'Overview'],
  ['spending', 'Spending'],
  ['debt', 'Debt'],
  ['emi', 'EMI Payments'],
  ['forecast', 'Forecast'],
  ['transactions', 'Transactions'],
  ['savings', 'Savings Accounts'],
  ['cards', 'Card Transactions'],
  ['upi', 'UPI Transactions'],
  ['files', 'Files & quality'],
  ['file-registry', 'Files & Passwords'],
  ['data-manager', 'Data Manager'],
  ['review-queue', 'Review Queue'],
  ['claims', 'Claims'],
  ['recurring', 'Recurring'],
  ['month-view', 'Month View'],
];"""

content = re.sub(r'const TABS = \[[^\]]+\];', tabs_new, content)

components_new = """
            {tab === 'files' && <Files data={data} />}
            {tab === 'file-registry' && <FilesAndPasswords />}
            {tab === 'data-manager' && <><WorkflowNav /><DataManager /></>}
            {tab === 'review-queue' && <ReviewQueue />}
            {tab === 'claims' && <Claims />}
            {tab === 'recurring' && <Recurring />}
            {tab === 'month-view' && <MonthView />}
"""

content = content.replace("{tab === 'files' && <Files data={data} />}\n            {tab === 'file-registry' && <FilesAndPasswords />}", components_new.strip())

with open(app_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated App.jsx")
