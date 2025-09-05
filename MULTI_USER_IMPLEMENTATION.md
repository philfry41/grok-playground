# Multi-User Story System Implementation

## 🎯 Project Overview
Implement Google OAuth + PostgreSQL for persistent, multi-user story system with story evolution over time.

## 📋 Implementation Phases

### Phase 1: Database Setup (Week 1)
**Status**: 🔄 In Progress
**Dependencies**: Render PostgreSQL service setup

#### Tasks:
- [ ] **1.1** Add PostgreSQL service to Render dashboard
- [ ] **1.2** Get DATABASE_URL from Render PostgreSQL service
- [ ] **1.3** Add DATABASE_URL to web service environment variables
- [ ] **1.4** Add database dependencies to requirements.txt
  - [ ] Flask-SQLAlchemy==3.0.5
  - [ ] Flask-Migrate==4.0.5
  - [ ] psycopg2-binary==2.9.7
- [ ] **1.5** Create database models (User, Story)
- [ ] **1.6** Set up database configuration in web_app.py
- [ ] **1.7** Initialize Flask-Migrate for database schema management
- [ ] **1.8** Create initial database migration
- [ ] **1.9** Test database connection and table creation
- [ ] **1.10** Deploy and verify database works on Render

#### Acceptance Criteria:
- ✅ PostgreSQL service running on Render
- ✅ Database connection established
- ✅ User and Story tables created
- ✅ Basic CRUD operations working

---

### Phase 2: Google OAuth Integration (Week 2)
**Status**: ⏳ Pending
**Dependencies**: Phase 1 complete

#### Tasks:
- [ ] **2.1** Set up Google OAuth credentials
  - [ ] Create Google Cloud Console project
  - [ ] Enable Google+ API
  - [ ] Create OAuth 2.0 credentials
  - [ ] Add redirect URIs for Render deployment
- [ ] **2.2** Add OAuth dependencies to requirements.txt
  - [ ] Flask-Dance==4.0.0
  - [ ] requests-oauthlib==1.3.1
- [ ] **2.3** Implement Google OAuth in web_app.py
  - [ ] Configure OAuth blueprint
  - [ ] Create login/logout routes
  - [ ] Handle OAuth callbacks
- [ ] **2.4** Create user session management
  - [ ] Store user info in session
  - [ ] Create user login/logout functions
  - [ ] Add user authentication decorators
- [ ] **2.5** Update story editor to require authentication
  - [ ] Add login requirement to story editor
  - [ ] Redirect unauthenticated users to login
  - [ ] Show user info in story editor
- [ ] **2.6** Test OAuth flow on local and Render
- [ ] **2.7** Handle OAuth errors and edge cases

#### Acceptance Criteria:
- ✅ Users can login with Google account
- ✅ User info stored in database
- ✅ Story editor requires authentication
- ✅ Login/logout flow works on Render

---

### Phase 3: Story Management (Week 3)
**Status**: ⏳ Pending
**Dependencies**: Phase 2 complete

#### Tasks:
- [ ] **3.1** Update story editor to save to database
  - [ ] Modify save_story_file() to use database
  - [ ] Associate stories with logged-in user
  - [ ] Handle story updates vs. new stories
- [ ] **3.2** Update story loading to use database
  - [ ] Modify list_story_files() to show user's stories
  - [ ] Update get_story_file() to load from database
  - [ ] Add public/private story filtering
- [ ] **3.3** Update chat system to load from database
  - [ ] Modify /loadstory command to use database
  - [ ] Update story context injection to use database
  - [ ] Handle story not found errors
- [ ] **3.4** Create user dashboard
  - [ ] Show user's stories list
  - [ ] Add story management (edit, delete, duplicate)
  - [ ] Add public/private toggle
  - [ ] Add story sharing functionality
- [ ] **3.5** Implement story sharing
  - [ ] Public stories accessible via story_id
  - [ ] Private stories only accessible by owner
  - [ ] Story access control in chat system
- [ ] **3.6** Add story versioning/history
  - [ ] Track story updates
  - [ ] Store story versions
  - [ ] Add version comparison

#### Acceptance Criteria:
- ✅ Stories saved to database with user ownership
- ✅ Users can manage their stories
- ✅ Public/private story sharing works
- ✅ Chat system loads stories from database
- ✅ Story evolution over time preserved

---

### Phase 4: Polish & Testing (Week 4)
**Status**: ⏳ Pending
**Dependencies**: Phase 3 complete

#### Tasks:
- [ ] **4.1** Error handling and validation
  - [ ] Database connection error handling
  - [ ] OAuth error handling
  - [ ] Story validation and error messages
  - [ ] User input validation
- [ ] **4.2** User experience improvements
  - [ ] Loading states and progress indicators
  - [ ] Success/error notifications
  - [ ] Responsive design for mobile
  - [ ] Accessibility improvements
- [ ] **4.3** Performance optimization
  - [ ] Database query optimization
  - [ ] Caching for frequently accessed stories
  - [ ] Image optimization for avatars
- [ ] **4.4** Security enhancements
  - [ ] Input sanitization
  - [ ] SQL injection prevention
  - [ ] XSS protection
  - [ ] CSRF protection
- [ ] **4.5** Testing and quality assurance
  - [ ] Unit tests for database operations
  - [ ] Integration tests for OAuth flow
  - [ ] End-to-end testing
  - [ ] Performance testing
- [ ] **4.6** Documentation and deployment
  - [ ] Update README with new features
  - [ ] Document API endpoints
  - [ ] Deployment checklist
  - [ ] User guide for story management

#### Acceptance Criteria:
- ✅ Robust error handling
- ✅ Smooth user experience
- ✅ Good performance
- ✅ Security best practices
- ✅ Comprehensive testing
- ✅ Complete documentation

---

## 🔧 Technical Requirements

### Database Schema
```sql
-- Users table
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    google_id VARCHAR(120) UNIQUE NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    name VARCHAR(120) NOT NULL,
    avatar_url VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stories table
CREATE TABLE stories (
    id SERIAL PRIMARY KEY,
    story_id VARCHAR(80) UNIQUE NOT NULL,
    title VARCHAR(200) NOT NULL,
    user_id INTEGER REFERENCES users(id) NOT NULL,
    content JSONB NOT NULL,
    is_public BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Environment Variables
```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID=your_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret
FLASK_SECRET_KEY=your_secret_key
```

### Dependencies
```txt
Flask-SQLAlchemy==3.0.5
Flask-Migrate==4.0.5
psycopg2-binary==2.9.7
Flask-Dance==4.0.0
requests-oauthlib==1.3.1
```

## 🎯 Success Metrics

### Phase 1 Success:
- Database connection established
- Tables created successfully
- Basic CRUD operations working

### Phase 2 Success:
- Google OAuth login working
- User sessions managed properly
- Story editor protected by authentication

### Phase 3 Success:
- Stories saved to database with user ownership
- Story sharing (public/private) working
- Chat system integrated with database

### Phase 4 Success:
- Robust error handling
- Good user experience
- Security best practices implemented
- Comprehensive testing completed

## 📝 Notes

### Current Status:
- ✅ Core story system working with file-based storage
- ✅ Scene state injection implemented
- ✅ Conversation persistence working
- 🔄 Ready to start Phase 1: Database Setup

### Key Decisions Made:
- ✅ Google OAuth for authentication
- ✅ PostgreSQL for database
- ✅ Public/private story sharing
- ✅ Story evolution over time
- ✅ Multi-user support

### Risks & Mitigation:
- **Risk**: OAuth setup complexity
  - **Mitigation**: Use Flask-Dance for simplified OAuth
- **Risk**: Database migration issues
  - **Mitigation**: Use Flask-Migrate for safe schema changes
- **Risk**: Render deployment issues
  - **Mitigation**: Test locally first, then deploy incrementally

## 🚀 Next Action
**Phase 1.1**: Add PostgreSQL service to Render dashboard and get DATABASE_URL
