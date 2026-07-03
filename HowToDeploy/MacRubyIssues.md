# iOS — Ruby & Build Environment Replication Guide

**Source machine:** Apple Silicon Mac | **Prepared:** June 2026
**Project:** https://github.com/MinowaHealth/MinowaMobile
---

## Background: Using Xcode for the mobile client.

The source Mac has three categories of Ruby, and knowing the difference saves a lot of pain when working on the mobile companion app. This is here as well as in the mobile app repo, they overlapped during early development and this is such a PITA we don't want anyone else to deal with it.

| Ruby | Path | Notes |
|------|------|-------|
| System Ruby 2.6 | `/usr/bin/ruby` | Apple's read-only copy shipped with macOS. This is where CocoaPods and all the gems from `gems.txt` are currently installed. |
| Homebrew portable-rubies | `/opt/homebrew/Library/Homebrew/vendor/portable-ruby/3.x.x/` | Homebrew's *internal* rubies (versions 3.3.5 through 3.4.8). Managed entirely by Homebrew itself — you don't install or maintain these. |
| Homebrew Cellar ruby | `/opt/homebrew/Cellar/ruby/3.4.7/` | User-facing ruby from `brew install ruby`. Present but not what CocoaPods uses on the source machine. |

> **The portable-ruby instances replicate automatically** when you install Homebrew on the new machine. Nothing to do there.

### Project versions at a glance

| Component | Version                      |
|-----------|------------------------------|
| React Native | 0.81.5                       |
| Expo SDK | ~54.0.33                     |
| CocoaPods | 1.16.2                       |
| iOS deployment target | 15.1                         |
| Xcode app target | `Your_Project_Name_Here`     |
| JS engine | Hermes                       |
| Architecture | arm64-darwin (Apple Silicon) |
| Bundler | 2.4.13                       |

---

## Step 1 — Xcode & Command Line Tools

1. Install Xcode from the Mac App Store (Xcode 14+; Xcode 15+ recommended for iOS 15.1 target).
2. Accept the license and install CLT:
   ```sh
   sudo xcodebuild -license accept
   xcode-select --install
   ```
3. Open Xcode once and let it finish installing any additional components.

---

## Step 2 — Homebrew

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

This also provisions all the Homebrew vendor portable-ruby instances automatically.

---

## Step 3 — Node.js

The project has no `.nvmrc`, but React Native 0.81 / Expo 54 work well with Node 20 LTS.

```sh
# via nvm (recommended):
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 20
nvm use 20

# or via Homebrew directly:
brew install node@20
```

---

## Step 4 — Ruby (use rbenv, not the system Ruby)

On the source machine, gems were installed into the system Ruby 2.6. On modern macOS the system Ruby is read-only without `sudo`, which is the root of the original pain. **On the new machine, use rbenv instead.** CocoaPods 1.16.2 requires Ruby >= 2.7, so Ruby 3.2.x works perfectly.

### 4a. Install rbenv

```sh
brew install rbenv ruby-build

# Add to your shell (zsh):
echo 'eval "$(rbenv init - zsh)"' >> ~/.zshrc
source ~/.zshrc
```

### 4b. Install Ruby 3.2.8

```sh
rbenv install 3.2.8
rbenv global 3.2.8

# Verify:
ruby --version   # → ruby 3.2.8 (...)
which ruby       # → ~/.rbenv/shims/ruby
```

### 4c. Install Bundler (pinned version)

```sh
gem install bundler -v 2.4.13
rbenv rehash
```

### 4d. Install CocoaPods 1.16.2

This single command pulls in nearly all the other required gems automatically as dependencies (xcodeproj, claide, molinillo, nanaimo, all the cocoapods-* plugins, nokogiri, ffi, etc.):

```sh
gem install cocoapods -v 1.16.2
rbenv rehash
```

### 4e. Install the remaining gems explicitly

A handful of gems from the source machine aren't pulled in transitively. Install them with pinned versions:

```sh
gem install activesupport  -v 6.1.7.10
gem install sqlite3        -v 1.3.13
gem install libxml-ruby    -v 3.2.1
gem install nokogiri       -v 1.13.8
gem install ffi            -v 1.17.2
gem install rexml          -v 3.4.4
gem install zeitwerk       -v 2.6.18
gem install tzinfo         -v 2.0.6
gem install i18n           -v 1.14.7
gem install concurrent-ruby -v 1.3.5
gem install connection_pool -v 2.5.4
gem install ruby2_keywords -v 0.0.5
gem install securerandom   -v 0.3.2
gem install base64         -v 0.3.0
gem install drb            -v 2.0.6
rbenv rehash
```

> `nokogiri` and `libxml-ruby` compile native extensions. If either fails, see Troubleshooting below.

### 4f. Verify

```sh
gem list | grep -E 'cocoapods |xcodeproj|bundler|ffi|nokogiri|activesupport'
```

---

## Step 5 — Project Setup

```sh
# Clone (or pull) the repo:
cd Project_Directory
git clone <repo-url> MinowaMobile
cd MinowaMobile

# Pin the project to your rbenv ruby (prevents accidental system-ruby use):
echo '3.2.8' > .ruby-version

# Install JS dependencies:
npm install
```

---

## Step 6 — pod install

```sh
cd Project_Directory/MinowaMobile/ios
bundle exec pod install --repo-update
```

`bundle exec` ensures CocoaPods runs under the rbenv ruby, not the system one. The first run downloads all pod specs and can take several minutes.

---

## Step 7 — Open in Xcode & Run

Always open the **workspace**, not the project file:

```sh
open Project_Directory/MinowaMobile/ios/MinowaMobile.xcworkspace
```

Select an iPhone 15+ simulator (iOS 15.1+), then **Product → Run** (⌘R).

---

## Full Gem Reference

Gems marked *auto* are pulled in as CocoaPods transitive dependencies and don't need explicit `gem install` calls.

| Gem | Version | How it arrives |
|-----|---------|---------------|
| `cocoapods` | 1.16.2 | `gem install` (explicit) |
| `bundler` | 2.4.13 | `gem install` (explicit) |
| `activesupport` | 6.1.7.10 | `gem install` (explicit) |
| `sqlite3` | 1.3.13 | `gem install` (explicit) |
| `libxml-ruby` | 3.2.1 | `gem install` (explicit) |
| `nokogiri` | 1.13.8 | `gem install` (explicit) |
| `ffi` | 1.17.2 | `gem install` (explicit) |
| `rexml` | 3.4.4 | `gem install` (explicit) |
| `zeitwerk` | 2.6.18 | `gem install` (explicit) |
| `tzinfo` | 2.0.6 | `gem install` (explicit) |
| `i18n` | 1.14.7 | `gem install` (explicit) |
| `concurrent-ruby` | 1.3.5 | `gem install` (explicit) |
| `connection_pool` | 2.5.4 | `gem install` (explicit) |
| `ruby2_keywords` | 0.0.5 | `gem install` (explicit) |
| `securerandom` | 0.3.2 | `gem install` (explicit) |
| `base64` | 0.3.0 | `gem install` (explicit) |
| `drb` | 2.0.6 | `gem install` (explicit) |
| `xcodeproj` | 1.27.0 | auto (CocoaPods dep) |
| `claide` | 1.1.0 | auto (CocoaPods dep) |
| `cocoapods-deintegrate` | 1.0.5 | auto (CocoaPods dep) |
| `cocoapods-downloader` | 2.1 | auto (CocoaPods dep) |
| `cocoapods-plugins` | 1.0.0 | auto (CocoaPods dep) |
| `cocoapods-search` | 1.0.1 | auto (CocoaPods dep) |
| `cocoapods-trunk` | 1.6.0 | auto (CocoaPods dep) |
| `cocoapods-try` | 1.2.0 | auto (CocoaPods dep) |
| `molinillo` | 0.8.0 | auto (CocoaPods dep) |
| `nanaimo` | 0.4.0 | auto (CocoaPods dep) |
| `CFPropertyList` | 2.3.6 | auto (CocoaPods dep) |
| `colored2` | 3.1.2 | auto (CocoaPods dep) |
| `fourflusher` | 2.3.1 | auto (CocoaPods dep) |
| `fuzzy_match` | 2.0.4 | auto (CocoaPods dep) |
| `gh_inspector` | 1.1.3 | auto (CocoaPods dep) |
| `nap` | 1.1.0 | auto (CocoaPods dep) |
| `netrc` | 0.11.0 | auto (CocoaPods dep) |
| `ruby-macho` | 2.5.1 | auto (CocoaPods dep) |
| `algoliasearch` | 1.27.5 | auto (CocoaPods dep) |
| `httpclient` | 2.9.0 | auto (CocoaPods dep) |
| `typhoeus` | 1.5.0 | auto (CocoaPods dep) |
| `ethon` | 0.15.0 | auto (CocoaPods dep) |
| `addressable` | 2.8.7 | auto (CocoaPods dep) |
| `public_suffix` | 4.0.7 | auto (CocoaPods dep) |
| `atomos` | 0.1.3 | auto (CocoaPods dep) |
| `escape` | 0.0.4 | auto (CocoaPods dep) |
| `mini_portile2` | 2.8.0 | auto (nokogiri dep) |
| `rake` | 12.3.3 | auto |
| `logger` | 1.7.0 | auto |
| `minitest` | 5.11.3 | auto |
| `net-telnet` | 0.2.0 | auto |
| `power_assert` | 1.1.3 | auto |
| `test-unit` | 3.2.9 | auto |

---

## Troubleshooting

**`pod` not found after install**
```sh
rbenv rehash
which pod   # should be ~/.rbenv/shims/pod
```

**Native extension build failure (`ffi`, `nokogiri`, `libxml-ruby`)**
```sh
# Confirm active CLT:
xcode-select -p
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

# For nokogiri on Apple Silicon if it's stubborn:
gem install nokogiri -v 1.13.8 -- --use-system-libraries=false
```

**CocoaPods picks up the wrong Ruby**
```sh
pod env | grep 'Ruby version'
# Should show 3.2.8 (or whatever rbenv version you installed)
```
If it shows 2.6.0, your rbenv shims aren't first on `PATH`. Make sure `eval "$(rbenv init - zsh)"` is in `~/.zshrc` and you've opened a new terminal.

**`incompatible architecture` during pod install**
The source machine's `ffi` is `arm64-darwin`. If pod install pulls a newer ffi, re-pin it and retry:
```sh
gem install ffi -v 1.17.2
rbenv rehash
cd ios && bundle exec pod install
```

**CocoaPods spec repo is stale**
```sh
pod repo update
cd ios && bundle exec pod install
```

**Hermes build errors in Xcode**
Delete Pods and reinstall cleanly:
```sh
cd ios
rm -rf Pods Podfile.lock
bundle exec pod install --repo-update
```

---

*Generated from source machine analysis — March 2026*
