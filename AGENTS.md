# SpendMind

## 1. Project Overview

SpendMind is a multi-user AI-powered personal expense
tracking and analytics assistant with a Telegram interface.

### Core Features

- User identification through Telegram.
- Expense tracking through manual input, natural language,
  receipt photos, and payment screenshots.
- AI-powered expense extraction and categorization.
- Expense confirmation, editing, and deletion.
- Potential duplicate expense detection.
- Multi-currency support with historical exchange rates.
- Item-level expense tracking.
- Custom user-defined expense categories.
- Shared product catalog with automatic classification.
- Natural language financial analytics.
- Conversation history and semantic memory.
- Strict user data isolation.

### Project Scope

The application focuses exclusively on expense tracking.

Income tracking, bank account integrations, budgeting,
and investment management are outside the MVP scope.

## 2. Technology Stack

### Backend
- Python 3.13
- FastAPI
- Pydantic
- Pydantic Settings
- SQLAlchemy (async)
- Alembic
- asyncpg
- httpx

### Database
- PostgreSQL
- pgvector

### AI Integration
- OpenAI API
- Structured Outputs
- Tool Calling
- Embeddings
- Retrieval-Augmented Generation (RAG)

### User Interface
- Telegram Bot API
- aiogram

### Development and Infrastructure
- uv
- Docker
- Docker Compose
- GitHub Actions
- Pytest
- pytest-asyncio
- Ruff
- OpenAI Codex

### Technology Constraints
- Use asynchronous I/O where appropriate.
- Do not introduce additional dependencies without approval.
- Do not introduce microservices, Redis, Celery, or MCP
  without explicit approval.
- Implement core functionality before introducing
  semantic search and advanced conversation memory.

## 2. Technology Stack

### Backend
- Python 3.13
- FastAPI
- Pydantic
- Pydantic Settings
- SQLAlchemy (async)
- Alembic
- asyncpg
- httpx

### Database
- PostgreSQL
- pgvector

### AI Integration
- OpenAI API
- Structured Outputs
- Tool Calling
- Embeddings
- Retrieval-Augmented Generation (RAG)

### User Interface
- Telegram Bot API
- aiogram

### Development and Infrastructure
- uv
- Docker
- Docker Compose
- GitHub Actions
- Pytest
- pytest-asyncio
- Ruff
- OpenAI Codex

### Technology Constraints
- Use asynchronous I/O where appropriate.
- Do not introduce additional dependencies without approval.
- Do not introduce microservices, Redis, Celery, or MCP
  without explicit approval.
- Implement core functionality before introducing
  semantic search and advanced conversation memory.

## 3. Architecture

### Architectural Approach

Use a modular monolith with clear separation of responsibilities.

The Telegram bot communicates with the backend through
the REST API and must not access the database directly.

### Application Layers

- API layer: endpoints, request validation, authentication.
- Service layer: business logic and operation orchestration.
- Repository layer: database queries and persistence.
- Integration layer: OpenAI API and exchange rate provider.
- Telegram bot layer: user interaction and notifications.

### Database Entities

- User: Telegram identity and account information.
- Expense: general expense information, currency,
  exchange rate, source, and confirmation status.
- ExpenseItem: individual items, quantity, measurement
  unit, amount, and category.
- Category: user-specific expense categories.
- Product: shared normalized product catalog.
- ProductType: shared product classification without
  hierarchical relationships.
- Message: conversation history and optional embeddings.

### Database Relationships

- User -> Expense: one-to-many.
- User -> Category: one-to-many.
- User -> Message: one-to-many.
- Expense -> ExpenseItem: one-to-many.
- Category -> ExpenseItem: one-to-many (optional).
- Product -> ExpenseItem: one-to-many (optional).
- ProductType -> Product: one-to-many.

### AI Architecture

- Structured Outputs for expense extraction.
- Tool Calling for selecting permitted backend operations.
- SQL aggregation for deterministic financial calculations.
- Sliding Window for initial conversation memory.
- Embeddings and semantic search for product matching
  and conversation retrieval.

### Architectural Constraints

- The LLM must not execute arbitrary SQL.
- Financial calculations must be performed by the backend.
- All user-specific operations must enforce data ownership.
- External integrations must be isolated from business logic.
- Database operations must use asynchronous SQLAlchemy.
- Multi-step database modifications must be transactional.

## 4. Business Rules

### Users and Data Isolation

- Identify users by their unique Telegram user ID.
- Automatically create a user profile on first interaction.
- Create default personal categories for each new user.
- Never expose another user's expenses, categories,
  or conversation history.
- Resolve user identity from trusted backend context,
  never from LLM-generated arguments.

### Expenses

- Support manual input, natural language, receipt photos,
  and payment screenshots.
- Every expense must contain at least one expense item.
- Use the following expense statuses:
  - pending
  - confirmed
  - rejected
- Require user confirmation before confirming an expense.
- Allow users to edit extracted data before confirmation.
- Include only confirmed expenses in financial analytics.
- Require confirmation before deleting expenses.
- Make expense confirmation atomic and idempotent.

### Financial Data

- Use Decimal in Python and NUMERIC in PostgreSQL.
- Never use floating-point arithmetic for monetary values.
- Preserve the original amount and currency.
- Use historical exchange rates based on the expense date.
- Perform currency conversion in the backend.
- Use UAH as the base currency.
- Validate that item totals match the expense total.
- Distribute explicit receipt-level discounts proportionally
  across expense items.
- Request clarification when totals cannot be reconciled.

### Duplicate Detection

- Compare amount, currency, date, merchant, and external
  transaction identifier when available.
- Warn users about potential duplicates.
- Never automatically reject an expense as a duplicate.

### Categories

- Categories belong to individual users.
- Category names must be unique within each user account.
- Allow users to create, rename, and delete categories.
- Allow expense items without a category.
- Before deleting a category with associated items,
  require reassignment or explicit removal of the association.
- Never delete expense items when deleting a category.

### Product Catalog

- Maintain a shared product catalog across all users.
- Preserve original item names extracted from receipts.
- Keep product classification independent of user categories.
- Use non-hierarchical product types.
- Initialize the catalog with common product types.
- Allow automatic extension while checking for duplicates.
- Never store personal user information in the shared catalog.

### AI and Analytics

- Treat AI-generated data as untrusted until validated.
- Use only explicitly permitted backend tools.
- Perform financial calculations through SQL or Python.
- Validate all tool arguments before execution.
- Request clarification when user intent is ambiguous.
- Retrieve current financial data from the database
  instead of relying on conversation memory.

## 4. Business Rules

### Users and Data Isolation

- Identify users by their unique Telegram user ID.
- Automatically create a user profile on first interaction.
- Create default personal categories for each new user.
- Never expose another user's expenses, categories,
  or conversation history.
- Resolve user identity from trusted backend context,
  never from LLM-generated arguments.

### Expenses

- Support manual input, natural language, receipt photos,
  and payment screenshots.
- Every expense must contain at least one expense item.
- Use the following expense statuses:
  - pending
  - confirmed
  - rejected
- Require user confirmation before confirming an expense.
- Allow users to edit extracted data before confirmation.
- Include only confirmed expenses in financial analytics.
- Require confirmation before deleting expenses.
- Make expense confirmation atomic and idempotent.

### Financial Data

- Use Decimal in Python and NUMERIC in PostgreSQL.
- Never use floating-point arithmetic for monetary values.
- Preserve the original amount and currency.
- Use historical exchange rates based on the expense date.
- Perform currency conversion in the backend.
- Use UAH as the base currency.
- Validate that item totals match the expense total.
- Distribute explicit receipt-level discounts proportionally
  across expense items.
- Request clarification when totals cannot be reconciled.

### Duplicate Detection

- Compare amount, currency, date, merchant, and external
  transaction identifier when available.
- Warn users about potential duplicates.
- Never automatically reject an expense as a duplicate.

### Categories

- Categories belong to individual users.
- Category names must be unique within each user account.
- Allow users to create, rename, and delete categories.
- Allow expense items without a category.
- Before deleting a category with associated items,
  require reassignment or explicit removal of the association.
- Never delete expense items when deleting a category.

### Product Catalog

- Maintain a shared product catalog across all users.
- Preserve original item names extracted from receipts.
- Keep product classification independent of user categories.
- Use non-hierarchical product types.
- Initialize the catalog with common product types.
- Allow automatic extension while checking for duplicates.
- Never store personal user information in the shared catalog.

### AI and Analytics

- Treat AI-generated data as untrusted until validated.
- Use only explicitly permitted backend tools.
- Perform financial calculations through SQL or Python.
- Validate all tool arguments before execution.
- Request clarification when user intent is ambiguous.
- Retrieve current financial data from the database
  instead of relying on conversation memory.

## 5. Code Standards

### General

- Follow PEP 8 and Python 3.13 conventions.
- Use explicit type hints for function parameters
  and return values.
- Use descriptive names for variables, functions,
  classes, and modules.
- Follow SOLID, DRY, KISS, and YAGNI principles.
- Avoid unnecessary abstractions and overengineering.
- Write docstrings for complex or non-obvious logic.

### Backend

- Keep API endpoints thin.
- Keep business logic in services.
- Keep database queries in repositories.
- Use dependency injection where appropriate.
- Use Pydantic schemas for request and response validation.
- Use SQLAlchemy Mapped and mapped_column declarations.
- Manage database schema changes through Alembic.
- Avoid blocking operations inside asynchronous functions.

### Error Handling and Security

- Handle expected exceptions explicitly.
- Never silently suppress exceptions.
- Use appropriate HTTP status codes.
- Store secrets in environment variables.
- Never commit credentials or API keys.
- Never log sensitive user data.
- Use UTC for stored timestamps.

### Testing

- Use pytest and pytest-asyncio.
- Test business logic, API endpoints, and database operations.
- Cover successful operations and edge cases.
- Mock external API integrations.
- Test user data isolation explicitly.
- Test duplicate detection and expense confirmation.
- Never modify tests solely to make incorrect code pass.

### Code Quality

- Use Ruff for linting and formatting.
- Run tests and linting before completing a task.
- Remove unused imports and dead code.
- Keep changes focused on the assigned task.

## 6. Agent Instructions

### Workflow

- Read AGENTS.md before starting any task.
- Inspect the existing code before making changes.
- Explain the implementation plan before writing code.
- Wait for approval before implementing the plan.
- Work on one assigned task at a time.
- Ask for clarification when requirements are ambiguous.
- Do not make undocumented assumptions.

### Restrictions

- Do not change architectural decisions without approval.
- Do not add dependencies without approval.
- Do not implement features outside the assigned task.
- Do not refactor unrelated code.
- Do not modify existing business rules without approval.
- Do not execute destructive commands without approval.
- Do not create commits, branches, or pull requests
  without explicit permission.
- Do not push changes to remote repositories.

### Task Completion

After completing a task, provide:

- A summary of implemented changes.
- A list of created and modified files.
- An explanation of important technical decisions.
- Test and linting results.
- Known limitations and potential risks.

Never claim that tests passed unless they were executed
successfully.

### Developer Ownership

The developer retains full control over architecture,
business requirements, and implementation decisions.

The agent must explain its decisions when requested,
including trade-offs and alternative approaches.

The goal is to accelerate development without replacing
the developer's understanding of the codebase.
