# Agent Flow

当前 LangGraph 的节点级流程：

```mermaid
flowchart TD
    A[load_skills<br/>扫描 skill 目录] --> B[route<br/>补全上下文并选择 skill]
    B -->|未命中 skill| E[final_answer<br/>普通对话回复]
    B -->|命中一个或多个 skill| C[execute_skill<br/>并行执行 skill 内部流程]
    C --> E
    E --> F[END]
```

`execute_skill` 节点会对命中的多个 skill 使用并行执行。每个 skill 的内部流程一致：

```mermaid
flowchart TD
    A[生成 Python 代码] --> B[执行代码]
    B -->|执行失败| C[评估失败原因<br/>flash/router_model]
    B -->|执行成功| D[评估结果是否回答问题<br/>flash/router_model]
    C -->|retry_code| E[带上一次代码/错误/评估原因重试一次]
    D -->|retry_code| E
    D -->|answer / partial / not_found / need_user_input| G[返回 skill_output]
    E --> H[再次执行代码]
    H --> I[再次评估]
    I --> G
    H -->|仍失败| J[返回 retry_failed 结构化结果]
    J --> G
```

当前实现里，评估逻辑还不是独立 LangGraph 节点，而是包在 `execute_skill` 节点内部。这样改动小，但图上看不出“执行 -> 评估 -> 是否回到执行”的显式边。

更理想的 LangGraph 形态可以拆成独立节点：

```mermaid
flowchart TD
    A[load_skills] --> B[route]
    B -->|未命中 skill| G[final_answer]
    B -->|命中 skill| C[execute_skill]
    C -->|可并行多个 skill| D[evaluate_result]
    D -->|answer / partial / not_found / need_user_input| G
    D -->|retry_code 且未重试| E[prepare_retry_feedback]
    E --> C
    D -->|retry_code 且已重试| G
    G --> H[END]
```

这个拆法更符合“执行节点后接评估节点，评估节点决定是否拉回执行节点重做”的 mental model，也更方便后续在前端显示当前阶段。
