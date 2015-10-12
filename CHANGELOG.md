# Changelog

### v0.0.8
- Replace data store with PostgreSQL/SQLAlchemy
- Implement a RESTful API
- Overhaul front end with new API-driven JS

### v0.0.7
- Fix 500 error when new project is saved with validation issue #220
- Create utility project #220
- Run utility projects given in `dockci.yaml` #223
- Add/retrieve generated data from utilities #225
- Add `skip_tests` option to `dockci.yaml` for use with utilites #223
- Specify `Dockerfile` to use in `dockci.yaml` #227
- Allow override of TLS params per Docker host #235
- Fix possible infinite loop on job page #239
- Job stage panel class is now md5 #239
- DockCI builds with utilites #240
- Replace Makefile with manage.sh #240

### v0.0.6
- Significant decrease in browser load on jobs with error stage #215
- Docker 1.7.0 (Server 1.19) #216

### v0.0.5 (Breaking)
- **BREAKING** Force project slugs to comply with Docker regex #192
  - All slugs will be lower-cased
  - Any characters that don't match `[a-z0-9-_.]` will be replaced with `_`
- Fix display of validation errors when saving a new project #192
- Add shields.io for projects #197
- Redirect to new project on creation #202
- Command output is a string, rather than a Python array dump #204
- Set mtime of files with ADD directive in a Dockerfile #206

### v0.0.4-1
- Fix issue browsing anonymously #187

## v0.0.4
- Upgrade YAML model #146
- Fix email #147
- Add users, related UI, protect some forms #147
- New job cancel button doesn't 404 #149
- Get GitHub OAuth token for users #149
- Import from GitHub on new job form #149, #155
- Add GitHub web hook on new job #149
- OAuth reauthenticate when scope changes #149
- Remove DEBIAN_MIRROR override #150
- Server host tests on config page #151
- GitHub recieves build status updates #161
- Rename "job" to "project" #172
- Rename "build" to "job" #173
- Rename "error" build state to "broken" #174
- Destructive operation confirmation template #178
- HMAC signed operations with expiry, user, model, allowed action #178
- Delete projects (along with associated jobs, and GitHub hooks) #178
- Sessions secret hidden on config edit form #178
- Don't roll up log when body element is clicked #179
- Better security for dowload of job output files #180
- Can't add a project that already exists #184

## v0.0.3
- Log level to debug #20
- Docker registry config options #20
- Failed/errored builds are cleaned, even when versioned #20
- Versioned images are pushed to registry #20
- Forms are validated for model integrity #21
- Added auto scroll button to build pages #22
- Auto scroll off by default #22
- Console line splitting fixed #23
- Version, and author info on builds list pages #24
- Add gravatars for git author/committer #25
- UI check boxes themed #27
- Add builds filter for only stable releases #28
- Remove Python2 dependency #28
- Add data migrations that occur on run #29
- Change build slugs from UUID1 to a hex string of create_ts values #29
- Take a tag reference instead of a hash #19
- Fix command output ordering in stage log #32
- Non-existent objects correctly 404 #33
- When build stages are clicked, they roll up #34
- When build stages are complete, they roll up unless "important" #34
- Build ancestor detection from git history #36
- Git changes from last ancestor in build log #36
- Pull Docker images on provision #39
- Use multiple Docker hosts #40
- Paginate the builds list page #44

## v0.0.2
- Streaming console #16
- HipChat notifications #9
- Correctly tag images #13
- Any git tags acceptable, not just semver (though semver is special) #12
- Tagged builds not cleaned up #4
- Tagged builds will remove built image before replacing #12
- Version tags can not be built twice #12
- Version tag builds will never use cache #3
- Service provisioning #10
- Ability to use some Docker env vars as config #8

## v0.0.1
- YAML model
- Web UI to add jobs, builds, global config
- Auto-detect if running in Docker, guess Docker host
- Web UI to view builds
- Git clone, checkout
- Check for git version tag, save to build
- Build the Docker image
- Run the Docker image with the `ci` command
- Success/failed/errored/running/queued statuses
- Build output archive/download
- GitHub push hook
- Builds save committer/auther info
