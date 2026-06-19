##########################
### RSS Feed Generation ##
##########################

.PHONY: feeds_generate_all
feeds_generate_all: ## Generate all RSS feeds
	$(call check_venv)
	$(call print_info_section,Generating all RSS feeds)
	$(Q)uv run feed_generators/run_all_feeds.py
	$(call print_success,All feeds generated)

.PHONY: feeds_anthropic_news
feeds_anthropic_news: ## Generate RSS feed for Anthropic News (incremental)
	$(call check_venv)
	$(call print_info,Generating Anthropic News feed)
	$(Q)uv run feed_generators/anthropic_news_blog.py
	$(call print_success,Anthropic News feed generated)

.PHONY: feeds_anthropic_news_full
feeds_anthropic_news_full: ## Generate RSS feed for Anthropic News (full reset)
	$(call check_venv)
	$(call print_info,Generating Anthropic News feed - FULL RESET)
	$(Q)uv run feed_generators/anthropic_news_blog.py --full
	$(call print_success,Anthropic News feed generated - full reset)

.PHONY: feeds_anthropic_engineering
feeds_anthropic_engineering: ## Generate RSS feed for Anthropic Engineering
	$(call check_venv)
	$(call print_info,Generating Anthropic Engineering feed)
	$(Q)uv run feed_generators/anthropic_eng_blog.py
	$(call print_success,Anthropic Engineering feed generated)

.PHONY: feeds_anthropic_research
feeds_anthropic_research: ## Generate RSS feed for Anthropic Research
	$(call check_venv)
	$(call print_info,Generating Anthropic Research feed)
	$(Q)uv run feed_generators/anthropic_research_blog.py
	$(call print_success,Anthropic Research feed generated)

.PHONY: feeds_anthropic_red
feeds_anthropic_red: ## Generate RSS feed for Anthropic Frontier Red Team
	$(call check_venv)
	$(call print_info,Generating Anthropic Red Team feed)
	$(Q)uv run feed_generators/anthropic_red_blog.py
	$(call print_success,Anthropic Red Team feed generated)

.PHONY: feeds_claude
feeds_claude: ## Generate RSS feed for Claude Blog (incremental)
	$(call check_venv)
	$(call print_info,Generating Claude Blog feed)
	$(Q)uv run feed_generators/claude_blog.py
	$(call print_success,Claude Blog feed generated)

.PHONY: feeds_claude_full
feeds_claude_full: ## Generate RSS feed for Claude Blog (full reset)
	$(call check_venv)
	$(call print_info,Generating Claude Blog feed - FULL RESET)
	$(Q)uv run feed_generators/claude_blog.py --full
	$(call print_success,Claude Blog feed generated - full reset)

.PHONY: clean_feeds
clean_feeds: ## Clean generated RSS feed files
	$(call print_warning,Removing generated RSS feeds)
	$(Q)rm -rf feeds/*.xml
	$(call print_success,RSS feeds removed)
