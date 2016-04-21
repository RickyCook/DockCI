# Changelog

### v0.0.10
- Allow override of repo name in `dockci.yaml` #397
- Messages when images will and won't be cleaned up #397
- Login, and register with GitHub and GitLab OAuth #398
- Configuration to turn off internal DockCI users #398
- Links to GitHub repos #399
- Basic parsing and folding of Docker build stage #400
- Shorter names for utility steps #401
- Failing utility doesn't create blank cache #402
- Allow project list API meta on filtered queries #404
- Create TS and commit ID in jobs list #405
- Auto detect websocket SSL #406
- Add "incomplete" state to projects meta #409
- Add git commit author avatar to job detail, and list APIs #412
- Add git commit author avatar to project latest job API #414
- API to retrieve the current time #417
- Job slug in projects latest job API #418
- Add tag, and branch to jobs list API #420
- Remove the ad-hoc job API from /projects/--/jobs/--.json #421
- Add avatar to user API #425
- Utilites copy permissions correctly #428
- API requests don't create new sessions #436
- Add 'order' option to projects list API #438
  - Value of 'recent' will order, descending by latest job create timestamps
- Job stream gives `null` for `init_*` when a job has no stages #444
- Change OAuth `return_to` param to `next` #445
- Add more error handling to OAuth #445

### v0.0.9
- Exception reported to Rollbar on git push hook `ValidationError` #322
- Support GitLab push/tag hooks #322
- Send HTTP 204 to GitHub ref deletions #322
- Add GitLab project type #323
- Push job status for GitLab projects #323
- Registry login #328
- Errors in Docker JSON stream fail stage automatically #328
- GitLab OAuth2 #329
- GitLab list, and fill projects #331
- External repos list correctly displays errors #331
- External repos list resets, aborts load on type change #331
- Can clone private GitHub, GitLab repos with OAuth #333
- Rename auth input names #338
- Authenticate registries where auth config exists for: #341
  - Push stage
  - Dockerfile FROM line
  - Utility images
  - Provisioned images
- Per-project registry push target #341
- Utilities require registry target to be set #341
- Validate unique values before DB commit in API #341
- Add "branch pattern" to allow floating tagged images for branches #345
- Fix tagged version rebuild #345
- Compare tagged versions vXXX and XXX as the same #345
- Jobs have only repo_fs, not repo any more #352
- Project list, detail include display_repo, and repo is removed #352
- Repo replaced with display repo in all of front end #352
- Count projects in all states in meta #354
- Add option to projects list API #355
- Skipped tests count as "good state" (pushable, etc) #357
- Significant simplification of image parsing #363
- If jobs fail before an image is built, doesn't try to clean up #365
- Services and utilities may be images to pull from a remote registry #383
- Utility output is cached based on image id, and input content hash #388
- Streaming logs replaced by RabbitMQ/websocket implementation #391
- Informational messages when a build can't be pushed #394
- Fix broken non-manual project editing from FE overhaul #396

### v0.0.8 (Breaking)
- **BREAKING** Replace data store with PostgreSQL/SQLAlchemy #260
  - No migration from yaml_model to PostgreSQL
- Implement a RESTful API #260
- Overhaul front end with new API-driven JS #260
- Revert to stock bootstap for ease of development #260
- Compose file for quickly starting a dev environment #260
- Use Flask-Script for better command integration #260
- Early job worker loop to handle dirty DB from the UI workers #260
- Handle mail failures in Flask-Security gracefully #266
- Move requirements to `pip-compile` syntax of new `pip-tools` #268
- Docker 1.8.3 (Server 1.20) #271
- New jobs will determine, store, and display branch name #276
- Job status notifications sent on "change in result" #279
- Fix several issues with GitHub statuses #307, #309
- Update Docker image name regex #311
- Roll back transactions when Flask requests are done #316
- Rollbar reporting #316
- Remove HipChat integration #316
- Move worker exception handling further up the stack #316

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
