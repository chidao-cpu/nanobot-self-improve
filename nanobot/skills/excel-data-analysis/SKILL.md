---
name: excel-data-analysis
description: Guide for analyzing Excel data using specialized tools (code_sandbox, excel_analyzer, sql_query).
always: true
---

# Excel Data Analysis Guide

When working with Excel files containing business or financial data, **always prefer using the specialized tools** over generic file reading and shell execution.

## Tool Selection Priority

### 0. Use `code_sandbox` for (HIGHEST PRIORITY for complex calculations):
- **Multi-step computations** that require intermediate results
- **Custom calculations** (ratios, percentages, differences between groups)
- **Statistical analysis** (standard deviation, median, percentiles, correlation)
- **Data transformations** (creating derived columns, pivoting with custom logic)
- **Cross-period comparisons** (month-over-month growth, year-over-year changes)
- **Complex aggregations** that SQL cannot express easily
- **Self-validation** (cross-checking results with alternative methods)

**Available modules**: `pandas` (as `pd`), `numpy` (as `np`), `math`, `json`, `statistics`, `datetime`, `re`, `collections`, `itertools`, `functools`

**Example (multi-step ratio calculation):**
```python
code_sandbox(
    code="""
import pandas as pd
df = pd.read_excel('data.xlsx')

# Filter and aggregate
v3_oct = df[(df['类型'].str.contains('机器人', na=False)) & (df['mon'] == '2024-10')]
v1_oct = df[(df['类型'] == '人工') & (df['mon'] == '2024-10')]

v3_d30 = v3_oct['D30累计支付金额'].sum()
v3_loan = v3_oct['订单金额'].sum()
v1_d30 = v1_oct['D30累计支付金额'].sum()
v1_loan = v1_oct['订单金额'].sum()

v3_rate = v3_d30 / v3_loan if v3_loan > 0 else 0
v1_rate = v1_d30 / v1_loan if v1_loan > 0 else 0

print(f"V3 D30回款率: {v3_rate:.4%}")
print(f"V1 D30回款率: {v1_rate:.4%}")
print(f"差距: {(v3_rate - v1_rate):.4%}")
""",
    description="Compare V3 vs V1 D30 collection rates"
)
```

**Example (statistical analysis):**
```python
code_sandbox(
    code="""
import pandas as pd
import numpy as np
df = pd.read_excel('data.xlsx')

# Monthly collection rates for robots
robot_df = df[df['类型'].str.contains('机器人', na=False)]
monthly = robot_df.groupby('mon').agg(
    d30_sum=('D30累计支付金额', 'sum'),
    loan_sum=('订单金额', 'sum')
)
monthly['rate'] = monthly['d30_sum'] / monthly['loan_sum']

print("Monthly D30 collection rates:")
print(monthly[['rate']].to_string())
print(f"\\nMean: {monthly['rate'].mean():.4%}")
print(f"Std: {monthly['rate'].std():.4%}")
print(f"Trend: {'↑' if monthly['rate'].iloc[-1] > monthly['rate'].iloc[0] else '↓'}")
""",
    description="Statistical analysis of monthly collection rates"
)
```

**When to prefer `code_sandbox` over `sql_query`:**
- Need to compute ratios/percentages between different filtered groups
- Need intermediate variables or multi-step logic
- Need statistical functions (std, median, percentile)
- Need to compare results across multiple time periods
- Need custom validation logic

### 1. Use `excel_analyzer` for:
- Reading Excel sheets and understanding structure
- Simple filtering rows by conditions
- Simple aggregations (sum, count, average) on the entire dataset
- Creating pivot tables
- Comparing datasets
- Analyzing trends over time

**Important: For filtered aggregations, use `sql_query` or `code_sandbox` instead!**

The `excel_analyzer` tool's `aggregate` operation works on the entire dataset. If you need to filter first and then aggregate, use `sql_query` or `code_sandbox`.

**Example (simple aggregation - all data):**
```
excel_analyzer(
    file_path="data.xlsx",
    operation="aggregate",
    sheet_name="Sheet1",
    agg_funcs={"amount": "sum", "count": "count"}
)
```

### 2. Use `sql_query` for:
- Complex multi-table joins
- Advanced filtering with SQL WHERE clauses
- Window functions and analytical queries
- When you need SQL's expressive power
- Simple single-query aggregations

**Example:**
```
sql_query(
    operation="import_excel",
    file_path="data.xlsx",
    table_name="data"
)
# Then query (ALWAYS use LIKE '机器人%' for type filtering):
sql_query(
    operation="query",
    sql="SELECT SUM(订单金额) FROM data WHERE 类型 LIKE '机器人%' AND mon='2024-10'"
)
```

## Available Data Files

### Primary Dataset
- **File**: `E:\之江实验室\nanobot-main\V1V3策略跟踪(对外版).xlsx`
- **IMPORTANT**: The filename has NO SPACE before the parenthesis. Use exactly `V1V3策略跟踪(对外版).xlsx`, NOT `V1V3策略跟踪 (对外版).xlsx`
- **Rows**: 31,941
- **Columns**: mon, 类型, queue_id_type, case_queue_tag_CODE, 客户等级, 客户数, 订单金额, d1-d30付款完成, D1-D30累计支付金额

### Column Mappings (Business Terms → Actual Columns)
- **贷款余额** = SUM(订单金额)
- **案件量** = SUM(客户数)
- **逾期金额** = SUM(订单金额) - SUM(D30累计支付金额)  （即未回收部分）
- **D30回款率** = D30累计支付金额 / 订单金额
- **D1回款率** = D1累计支付金额 / 订单金额

### Type Filtering Rules (CRITICAL)
The `类型` column has these values: `机器人`, `机器人1-3`, `机器人1-4`, `机器人1-5`, `机器人1-6`, `机器人1-10`, `人工`, and `NaN`.

**When the question says "类型为机器人", you MUST use `LIKE '机器人%'` to match ALL robot subtypes:**
```sql
-- ✅ CORRECT: matches 机器人, 机器人1-3, 机器人1-4, etc.
WHERE 类型 LIKE '机器人%'

-- ❌ WRONG: only matches exact '机器人', misses subtypes
WHERE 类型 = '机器人'
```

### Quarterly Date Filtering
The `mon` column uses format `YYYY-MM`. For quarterly queries:
```sql
-- Q4 (4季度): months 10, 11, 12
WHERE mon IN ('2024-10', '2024-11', '2024-12')

-- Q3 (3季度): months 7, 8, 9
WHERE mon IN ('2024-07', '2024-08', '2024-09')

-- Q2 (2季度): months 4, 5, 6
WHERE mon IN ('2024-04', '2024-05', '2024-06')

-- Q1 (1季度): months 1, 2, 3
WHERE mon IN ('2024-01', '2024-02', '2024-03')
```

### Complete SQL Templates

**Monthly query (e.g., October 2024 robot loan balance):**
```sql
SELECT SUM(订单金额) as loan_balance
FROM data
WHERE 类型 LIKE '机器人%' AND mon = '2024-10'
```

**Quarterly query (e.g., Q4 2024 robot case count):**
```sql
SELECT SUM(客户数) as case_count
FROM data
WHERE 类型 LIKE '机器人%' AND mon IN ('2024-10', '2024-11', '2024-12')
```

**Overdue amount query (e.g., October 2024 robot overdue):**
```sql
SELECT SUM(订单金额) - SUM(D30累计支付金额) as overdue_amount
FROM data
WHERE 类型 LIKE '机器人%' AND mon = '2024-10'
```

### Reference Files
- **Questions**: `E:\之江实验室\nanobot-main\问题及备注.xlsx` (30 test questions)
- **Answers**: `E:\之江实验室\nanobot-main\答案.xlsx` (expected answers for validation)

## Workflow Pattern

When answering questions about the data:

1. **Understand the question** - Identify what metric is being asked for
2. **Map to columns** - Translate business terms to actual column names (see Column Mappings above)
3. **Choose the right tool**:
   - Simple single-metric query → `sql_query`
   - Multi-step calculation, ratios, comparisons → `code_sandbox`
   - Statistical analysis (std, median, trend) → `code_sandbox`
   - Just reading data structure → `excel_analyzer`
4. **Execute the analysis** - Run the query or code
5. **Report the result** - Provide the answer with context

## Common Pitfalls to Avoid

❌ **Don't** add spaces in filenames — `V1V3策略跟踪(对外版).xlsx` is correct, `V1V3策略跟踪 (对外版).xlsx` is WRONG
❌ **Don't** use `WHERE 类型 = '机器人'` — this misses subtypes like 机器人1-3, 机器人1-4, etc.
❌ **Don't** confuse 逾期金额 with 订单金额 — 逾期金额 = SUM(订单金额) - SUM(D30累计支付金额)
❌ **Don't** use `read` tool to read Excel files as text (they're binary)
❌ **Don't** use `exec` to write Python scripts — use `code_sandbox` instead
❌ **Don't** assume column names match business terms exactly
❌ **Don't** do multi-step arithmetic in your head — use `code_sandbox` for precise computation
✅ **Do** always use `WHERE 类型 LIKE '机器人%'` for robot type filtering (in SQL)
✅ **Do** use `df['类型'].str.contains('机器人', na=False)` for robot type filtering (in code_sandbox)
✅ **Do** use `sql_query` for simple single-query aggregations
✅ **Do** use `code_sandbox` for multi-step calculations, ratios, and comparisons
✅ **Do** re-check your query if the result looks wrong

## Example: Answering "What is the total loan balance?"

**Step 1**: Understand - "贷款余额" means total loan amount
**Step 2**: Map - 贷款余额 = SUM(订单金额)
**Step 3**: Choose tool - Simple aggregation → `excel_analyzer`
**Step 4**: Execute:
```
excel_analyzer(
    file_path="E:\\之江实验室\\nanobot-main\\V1V3策略跟踪(对外版).xlsx",
    operation="aggregate",
    agg_funcs={"订单金额": "sum"}
)
```
**Step 5**: Report the answer
