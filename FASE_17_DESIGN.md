# FASE 17 — DESIGN DOCUMENT: QUESTION BANK & CLASSIFICATION

**Data**: 31 de Agosto de 2026  
**Status**: Design In Progress  
**Target**: Generic, discipline-agnostic Question Bank with governance

---

## 1. CURRENT STATE (AUDIT RESULTS)

### Existing Models ✓
- **Question** - minimal (id, validation_status, created_at)
- **QuestionVersion** - comprehensive (version_kind, canonical_text, options, etc.)
- **QuestionClassification** - exists but not fully integrated
- **StudentContentMastery** - for mastery tracking
- **LearningHistory** - for audit trail
- **CatalogNode** - for hierarchy
- **Taxonomy** / **TaxonomyNode** - for BNCC and other frameworks
- **EducationalResource** - generic resource model
- **AdminAuditLog** - audit infrastructure

### Critical Gaps 🔴
1. **Question lacks multi-tenant fields**
   - [ ] school_id (for school-owned questions)
   - [ ] author_external_id
   - [ ] owner_external_id
   - [ ] origin_type (PLATFORM, SCHOOL, TEACHER, IMPORTED, GENERATED)
   - [ ] visibility_scope (PRIVATE, SCHOOL, PUBLIC)

2. **Question lacks governance fields**
   - [ ] status (DRAFT, REVIEW, APPROVED, PUBLISHED, ARCHIVED, REJECTED)
   - [ ] created_by_external_identity
   - [ ] question_type (MULTIPLE_CHOICE, ESSAY, etc.)
   - [ ] external_id/canonical_id for federation

3. **QuestionClassification is not linked to Question**
   - Current: Links to QuestionVersion only
   - Needed: Explicit Question → Classification path

4. **Missing elegibility determination**
   - No formal rules about when a question can be used in practice

5. **Missing approval workflow**
   - No QuestionApproval or QuestionStatusTransition model

6. **Multi-tenant isolation not enforced**
   - Questions from other schools should not leak

---

## 2. PHASE 17 ARCHITECTURE

### 2.1 Question Model Evolution

```
Current:
┌─────────────────────┐
│      Question       │
├─────────────────────┤
│ id (PK)             │
│ validation_status   │
│ created_at          │
├─────────────────────┤
│ versions (1:N)      │
└─────────────────────┘

Needed:
┌─────────────────────────────────────┐
│           Question                  │
├─────────────────────────────────────┤
│ id (PK)                             │
│ external_id (optional, unique)      │
│ question_type (MULTIPLE_CHOICE)     │
│ school_id (FK, nullable)            │ ← Multi-tenant
│ created_by_external_identity        │ ← Author
│ author_external_id                  │ ← Author ID
│ owner_external_id (role-based)      │ ← Owner for school questions
│ origin_type (PLATFORM|SCHOOL|...)   │ ← Origin
│ status (DRAFT|REVIEW|APPROVED|...)  │ ← Governance
│ visibility_scope (PRIVATE|SCHOOL|...|PUBLIC) ← Access
│ validation_status (from before)     │ ← Backward compat
│ metadata_                           │ ← Extensibility
│ created_at                          │
│ updated_at                          │
├─────────────────────────────────────┤
│ versions (1:N)                      │
│ classifications (1:N)               │
│ status_transitions (1:N)            │
│ approvals (1:N)                     │
│ audit_logs (1:N)                    │
└─────────────────────────────────────┘
```

### 2.2 New Models Required

#### QuestionStatusTransition
Tracks status changes with audit trail.

```sql
CREATE TABLE question_status_transitions (
  id UUID PRIMARY KEY,
  question_id UUID NOT NULL (FK → questions.id),
  from_status VARCHAR(30),
  to_status VARCHAR(30) NOT NULL,
  performed_by_external_id VARCHAR(255),
  reason TEXT,
  metadata JSONB,
  created_at TIMESTAMP,
  CONSTRAINT fk_status_transitions_question FOREIGN KEY (question_id)
);
```

#### QuestionApproval
Tracks approvals/rejections/feedback.

```sql
CREATE TABLE question_approvals (
  id UUID PRIMARY KEY,
  question_id UUID NOT NULL (FK → questions.id),
  version_id UUID NOT NULL (FK → question_versions.id),
  reviewer_external_id VARCHAR(255) NOT NULL,
  decision VARCHAR(20) NOT NULL (APPROVED|REJECTED|FEEDBACK),
  feedback_text TEXT,
  approved_at TIMESTAMP,
  created_at TIMESTAMP,
  CONSTRAINT fk_approvals_question FOREIGN KEY (question_id)
);
```

#### QuestionEligibility
Cached/computed eligibility status for performance.

```sql
CREATE TABLE question_eligibility (
  id UUID PRIMARY KEY,
  question_id UUID NOT NULL UNIQUE (FK → questions.id),
  is_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  reasons TEXT[] (array of reasons why eligible or not),
  last_checked_at TIMESTAMP,
  valid_until TIMESTAMP,
  created_at TIMESTAMP
);
```

### 2.3 QuestionClassification Enhancement

**Current**: Links QuestionVersion → TaxonomyNode  
**Enhanced**: Add metadata about the classification

```sql
ALTER TABLE question_classifications ADD COLUMN (
  classification_confidence NUMERIC(3,2),  -- 0.0 to 1.0
  classified_by VARCHAR(50),  -- TEACHER|PLATFORM|AI|MANUAL
  ai_model VARCHAR(255),  -- If AI-generated
  human_verified BOOLEAN DEFAULT FALSE,
  approval_status VARCHAR(20),  -- PENDING|APPROVED|REJECTED
  CONSTRAINT fk_classifications_question_version FOREIGN KEY (question_version_id)
);
```

---

## 3. MULTI-TENANT RULES

### 3.1 Question Ownership

```
ORIGIN_TYPE = PLATFORM
  → school_id NULL
  → visible to all (public questions)
  → modified by PLATFORM_ADMIN only

ORIGIN_TYPE = SCHOOL
  → school_id NOT NULL
  → visible to that school's users (with scope checks)
  → modified by DIRECTOR/TEACHER of that school

ORIGIN_TYPE = TEACHER
  → school_id NOT NULL
  → owner_external_id = teacher_id
  → visible to that teacher and coordinators of that school
  → modified by teacher or their school admins

ORIGIN_TYPE = IMPORTED
  → preserve original_source metadata
  → school_id may be set if imported into specific school
  → modified by admin of owning entity

ORIGIN_TYPE = GENERATED
  → mark with ai_model in metadata
  → must pass review/approval before becoming PUBLISHED
  → school_id context-dependent
```

### 3.2 Visibility & Access Rules

```
VISIBILITY_SCOPE = PRIVATE
  → only owner + admins
  
VISIBILITY_SCOPE = CLASSROOM
  → only users with ROLE in that classroom scope
  
VISIBILITY_SCOPE = SCHOOL
  → only users of that school
  
VISIBILITY_SCOPE = PUBLIC
  → all authenticated users (including independent students)
```

---

## 4. STATUS & GOVERNANCE WORKFLOW

```
DRAFT (default for created questions)
  ↓
  [SUBMIT FOR REVIEW]
  ↓
REVIEW (waiting for approval)
  ↓
  ├─→ [APPROVE] → APPROVED
  ├─→ [REJECT] → REJECTED (terminal)
  └─→ [REQUEST CHANGES] → DRAFT
  
APPROVED
  ↓
  [PUBLISH]
  ↓
PUBLISHED (available for use)
  ↓
  [ARCHIVE]
  ↓
ARCHIVED (terminal, cannot be used)

Rules:
- Only PUBLISHED questions can be used in practice
- REJECTED questions are terminal (cannot resurrect)
- ARCHIVED questions remain in DB for audit, not usable
- Status changes create AdminAuditLog entries
```

---

## 5. ELEGIBILITY RULES

A question is elegible for practice if:

```python
is_eligible = all([
    question.status == 'PUBLISHED',
    question_version.version_kind == 'official_original',
    question.validation_status in ['valid', 'acceptable'],
    question_version.recommended_difficulty is not None,
    classification_exists(question_version),
    not is_archived(question),
    user_can_view(question, current_user),
    question.visibility_scope matches user.scope,
])
```

---

## 6. QUESTION CLASSIFICATION LINK

Current structure links `QuestionVersion` → `TaxonomyNode`.

**Needed**: Question → TaxonomyNode relationship for:
- Discipline
- Grade level
- Subject/competency

Approach:
```
Question
  → TaxonomyNode (primary_discipline)
  
QuestionVersion
  → TaxonomyNode[] (specific skills/competencies covered)
```

---

## 7. API ENDPOINTS (HIGH LEVEL)

**Not implemented yet**, design outline:

```
POST   /api/v1/questions
       Create new question (DRAFT status)

GET    /api/v1/questions
       List with filters (discipline, grade, status, etc.)
       Respect multi-tenant + role scopes

GET    /api/v1/questions/{question_id}
       Get question + classification + eligibility

PATCH  /api/v1/questions/{question_id}
       Update question (only if DRAFT or owned by user)

POST   /api/v1/questions/{question_id}/versions
       Create new version

POST   /api/v1/questions/{question_id}/submit-review
       Change status DRAFT → REVIEW

POST   /api/v1/questions/{question_id}/approve
       PLATFORM_ADMIN or DIRECTOR: REVIEW → APPROVED

POST   /api/v1/questions/{question_id}/reject
       PLATFORM_ADMIN or DIRECTOR: REVIEW → REJECTED

POST   /api/v1/questions/{question_id}/publish
       APPROVED → PUBLISHED

POST   /api/v1/questions/{question_id}/archive
       PUBLISHED → ARCHIVED

POST   /api/v1/questions/{question_id}/classify
       Associate with TaxonomyNode

GET    /api/v1/questions/{question_id}/eligibility
       Check if question can be used in practice
```

---

## 8. TESTS REQUIRED

### 8.1 Multi-Tenant Isolation
- [ ] School A user cannot see/edit School B questions (PRIVATE)
- [ ] School A user cannot edit PLATFORM questions
- [ ] Independent student can see PUBLIC questions
- [ ] Independent student cannot see SCHOOL questions

### 8.2 Status & Governance
- [ ] Question starts in DRAFT
- [ ] DRAFT questions not visible to students
- [ ] Status transitions follow workflow
- [ ] REJECTED is terminal
- [ ] ARCHIVED is terminal
- [ ] Transitions create audit logs

### 8.3 Eligibility
- [ ] PUBLISHED + valid version + classified = eligible
- [ ] DRAFT question not eligible
- [ ] ARCHIVED question not eligible
- [ ] Missing classification → not eligible
- [ ] Missing difficulty → not eligible

### 8.4 Classification
- [ ] Question can be classified to multiple TaxonomyNodes
- [ ] Classification can be rejected/approved
- [ ] AI-generated classifications marked as such

### 8.5 Integration
- [ ] Practice session can select from eligible questions
- [ ] StudentContentMastery updated correctly
- [ ] LearningHistory tracks question + difficulty + content

### 8.6 Authorization
- [ ] TEACHER can create/edit own questions
- [ ] COORDINATOR can view/manage school questions
- [ ] PLATFORM_ADMIN can view/manage all questions
- [ ] Question author can update DRAFT questions
- [ ] ROLE + SCOPE enforced for all operations

---

## 9. DATABASE MIGRATIONS

### Migration 016: Enhance Question Model & Add Governance

```python
# Revision: 016_question_governance
# Down revision: 015_user_invitations

def upgrade() -> None:
    # 1. Add columns to questions table
    op.add_column('questions', sa.Column('external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('question_type', sa.String(50), nullable=False, server_default='MULTIPLE_CHOICE'))
    op.add_column('questions', sa.Column('school_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('questions', sa.Column('author_external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('owner_external_id', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('origin_type', sa.String(50), nullable=False, server_default='PLATFORM'))
    op.add_column('questions', sa.Column('status', sa.String(30), nullable=False, server_default='DRAFT'))
    op.add_column('questions', sa.Column('visibility_scope', sa.String(50), nullable=False, server_default='PRIVATE'))
    op.add_column('questions', sa.Column('created_by_external_identity', sa.String(255), nullable=True))
    op.add_column('questions', sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'))
    op.add_column('questions', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=func.now()))
    
    # 2. Add indexes
    op.create_index('ix_questions_school_id', 'questions', ['school_id'])
    op.create_index('ix_questions_status', 'questions', ['status'])
    op.create_index('ix_questions_origin_type', 'questions', ['origin_type'])
    op.create_index('ix_questions_created_by', 'questions', ['created_by_external_identity'])
    
    # 3. Add constraints
    op.create_check_constraint('ck_questions_origin_type', 'questions', 
        "origin_type IN ('PLATFORM', 'SCHOOL', 'TEACHER', 'IMPORTED', 'GENERATED')")
    op.create_check_constraint('ck_questions_status', 'questions',
        "status IN ('DRAFT', 'REVIEW', 'APPROVED', 'PUBLISHED', 'ARCHIVED', 'REJECTED')")
    op.create_check_constraint('ck_questions_visibility_scope', 'questions',
        "visibility_scope IN ('PRIVATE', 'CLASSROOM', 'SCHOOL', 'PUBLIC')")
    
    # 4. Create new tables
    op.create_table(
        'question_status_transitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('from_status', sa.String(30), nullable=True),
        sa.Column('to_status', sa.String(30), nullable=False),
        sa.Column('performed_by_external_id', sa.String(255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata_', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='RESTRICT'),
    )
    op.create_index('ix_question_status_transitions_question_id', 'question_status_transitions', ['question_id'])
    
    # 5. Similar for question_approvals, question_eligibility
    # ... (continued in actual migration file)
```

---

## 10. IMPLEMENTATION CHECKLIST

### Phase 17A: Core Model Enhancement
- [ ] Migration 016: Create/enhance tables
- [ ] Update Question model
- [ ] Create QuestionStatusTransition model
- [ ] Create QuestionApproval model
- [ ] Create QuestionEligibility model
- [ ] Update QuestionClassification with new fields

### Phase 17B: Services & Logic
- [ ] QuestionService enhancements
- [ ] Authorization service integration
- [ ] Status workflow service
- [ ] Eligibility calculator
- [ ] Classification service

### Phase 17C: APIs
- [ ] REST endpoints for CRUD
- [ ] Status transition endpoints
- [ ] Classification endpoints
- [ ] Search/filter endpoints
- [ ] Eligibility check endpoint

### Phase 17D: Testing
- [ ] Multi-tenant isolation tests
- [ ] Status workflow tests
- [ ] Authorization tests
- [ ] Eligibility tests
- [ ] Integration with practice/mastery

### Phase 17E: Documentation
- [ ] API documentation
- [ ] Model relationships diagram
- [ ] Workflow diagrams
- [ ] Integration guide

---

## 11. BACKWARD COMPATIBILITY

**Critical**: No breaking changes to existing flows.

- QuestionVersion remains unchanged
- Existing practice sessions continue to work
- Existing StudentContentMastery unaffected
- LearningHistory can link to enhanced Question model
- Existing QuestionClassification still works

**How**: Add new fields as nullable (with sensible defaults) and fill via data migration script.

---

## 12. NEXT STEPS

1. **Finalize design** with review of this document
2. **Create migration 016** with all new tables/columns
3. **Implement models** for Question, QuestionStatusTransition, etc.
4. **Implement services** for status workflow, eligibility, etc.
5. **Write tests** for all scenarios
6. **Validate PostgreSQL** migration on temporary DB
7. **Full test suite** (391+ existing + new Fase 17 tests)
8. **Report** with evidence of all gates passing
