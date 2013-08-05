# LiveStyle for Sublime Text

[Emmet LiveStyle](http://livestyle.emmet.io) is a plugin for live bi-directional (editor↔browser) CSS editing of new generation. You have to install [browser plugins](http://livestyle.emmet.io/install/) to work with this extension.

## Installation

You can install directly from Package Control.

1. Install [Package Control](http://wbond.net/sublime_packages/package_control/installation) first.
2. When installed, open Command Palette in ST editor and pick `Package Control: Install Repository` command.
3. Find and install *Emmet LiveStyle* extension.

When installed, LiveStyle will automatically download require PyV8 extension. If you experience issues with automatic PyV8 installation, try to [install it manually](https://github.com/emmetio/pyv8-binaries#manual-installation).

*NB: if you have Emmet or TernJS extensions installed, make sure you have the most recent versions since they contain updates vital for LiveStyle extension.*

## Installing LiveStyle for WebKit extension

On OSX you can install LiveStyle extension for WebKit for live editing of iOS web apps. Since Safari/WebKit doesn’t provide API to extend Web Inspector, you have to *hack it*. 

This plugin provides automatic installer of WebKit extension. To use it, simply run `Tools > Install LiveSTyle for WebKit extension` menu item or `LiveStyle: Install WebKit extension` command from Command Palette.

[Read more about WebKit extension](http://livestyle.emmet.io/install/#safari-extension).