"""
Standalone test script for QuestionExtractor.

This script tests the question extraction functionality for radio buttons and checkboxes
on real job application forms.

Usage:
    python test_question_extractor.py <job_url>
    
Example:
    python test_question_extractor.py "https://boards.greenhouse.io/example/jobs/12345"
"""
import asyncio
import sys
import os
from pathlib import Path
import argparse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from loguru import logger
from Agents.components.executors.question_extractor import QuestionExtractor


async def test_question_extraction_on_url(url: str, keep_open: bool = True):
    """Test the QuestionExtractor on a real job application form."""
    logger.info("=" * 80)
    logger.info("QUESTION EXTRACTOR TEST - REAL JOB APPLICATION FORM")
    logger.info("=" * 80)
    logger.info(f"URL: {url}")
    logger.info("=" * 80 + "\n")
    
    async with async_playwright() as p:
        # Launch browser (visible so you can see the page)
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        # Set a reasonable viewport
        await page.set_viewport_size({"width": 1280, "height": 720})
        
        try:
            # Navigate to the URL
            logger.info(f"🌐 Navigating to: {url}")
            await page.goto(url, wait_until="networkidle", timeout=30000)
            logger.info("✅ Page loaded successfully\n")
            
            # Wait a bit for any dynamic content to load
            await asyncio.sleep(2)
            
            # Initialize QuestionExtractor
            extractor = QuestionExtractor(page)
            
            # Find all radio buttons on the page
            all_radios = await page.locator('input[type="radio"]').all()
            logger.info(f"📻 Found {len(all_radios)} radio buttons on the page")
            
            # Find all checkboxes on the page
            all_checkboxes = await page.locator('input[type="checkbox"]').all()
            logger.info(f"☑️  Found {len(all_checkboxes)} checkboxes on the page\n")
            
            # Group radio buttons by name (they share the same name)
            radio_groups = {}
            for radio in all_radios:
                try:
                    name = await radio.get_attribute('name')
                    if name:
                        if name not in radio_groups:
                            radio_groups[name] = []
                        radio_groups[name].append(radio)
                except Exception:
                    pass
            
            # Group checkboxes intelligently (by question, not just by name)
            logger.info("🔍 Grouping checkboxes by question...")
            checkbox_groups = await extractor.group_checkboxes_by_question(all_checkboxes)
            logger.info(f"✅ Found {len(checkbox_groups)} checkbox groups\n")
            
            # Test radio button groups
            if radio_groups:
                print("=" * 80)
                print("RADIO BUTTON GROUPS")
                print("=" * 80 + "\n")
                
                for idx, (name, radios) in enumerate(radio_groups.items(), 1):
                    try:
                        # Extract question using the first radio in the group
                        result = await extractor.extract_question_for_field(radios[0], 'radio')
                        
                        print(f"Radio Group #{idx}: name='{name}'")
                        print("-" * 80)
                        
                        if result['question']:
                            print(f"✅ Question: {result['question']}")
                            print(f"   Source: {result['questionSource']}")
                        else:
                            print(f"⚠️  Question: (not found)")
                        
                        print(f"   Number of options: {len(result['allOptions'])}")
                        
                        if result['allOptions']:
                            print(f"   Options:")
                            for opt in result['allOptions']:
                                opt_text = opt.get('text', 'N/A')
                                opt_value = opt.get('value', 'N/A')
                                opt_id = opt.get('id', 'N/A')
                                print(f"      • {opt_text}")
                                print(f"        (value='{opt_value}', id='{opt_id}')")
                        else:
                            # If QuestionExtractor didn't find options, list them manually
                            print(f"   Options (manual detection):")
                            for radio in radios:
                                try:
                                    radio_id = await radio.get_attribute('id')
                                    radio_value = await radio.get_attribute('value')
                                    
                                    # Try to get label
                                    radio_label = ''
                                    if radio_id:
                                        label = page.locator(f'label[for="{radio_id}"]').first
                                        if await label.count() > 0:
                                            radio_label = await label.text_content()
                                    
                                    if not radio_label:
                                        radio_label = await radio.get_attribute('aria-label') or 'No label'
                                    
                                    print(f"      • {radio_label}")
                                    print(f"        (value='{radio_value}', id='{radio_id}')")
                                except Exception as e:
                                    print(f"      • (error reading option: {e})")
                        
                        print()
                        
                    except Exception as e:
                        print(f"Radio Group #{idx}: name='{name}'")
                        print(f"❌ Error extracting question: {e}\n")
            else:
                print("\n⚠️  No radio button groups found on this page.\n")
            
            # Test checkbox groups
            if checkbox_groups:
                print("=" * 80)
                print("CHECKBOX GROUPS")
                print("=" * 80 + "\n")
                
                for idx, group in enumerate(checkbox_groups, 1):
                    try:
                        question = group.get('question', '')
                        question_source = group.get('question_source', 'unknown')
                        checkboxes = group.get('checkboxes', [])
                        
                        print(f"Checkbox Group #{idx}")
                        print("-" * 80)
                        
                        if question:
                            print(f"✅ Question: {question}")
                            print(f"   Source: {question_source}")
                        else:
                            print(f"⚠️  Question: (not found)")
                        
                        print(f"   Number of checkboxes: {len(checkboxes)}")
                        print(f"   Options:")
                        
                        for cb_data in checkboxes:
                            cb_label = cb_data.get('label', 'No label')
                            cb_name = cb_data.get('name', 'N/A')
                            cb_id = cb_data.get('id', 'N/A')
                            cb_value = cb_data.get('value', 'N/A')
                            
                            print(f"      • {cb_label}")
                            print(f"        (name='{cb_name}', value='{cb_value}', id='{cb_id}')")
                        
                        print()
                        
                    except Exception as e:
                        print(f"Checkbox Group #{idx}")
                        print(f"❌ Error displaying group: {e}\n")
            else:
                print("\n⚠️  No checkbox groups found on this page.\n")
            
            print("=" * 80)
            print("TEST COMPLETE")
            print("=" * 80 + "\n")
            
            if keep_open:
                # Keep browser open for inspection
                logger.info("Browser will remain open for inspection.")
                logger.info("Press Ctrl+C in the terminal to close the browser and exit.")
                try:
                    # Keep alive until user interrupts
                    while True:
                        await asyncio.sleep(1)
                except KeyboardInterrupt:
                    logger.info("\n👋 Closing browser...")
            else:
                logger.info("Closing browser in 5 seconds...")
                await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"❌ Error during test: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await browser.close()


async def main():
    """Run the test with command line arguments."""
    parser = argparse.ArgumentParser(
        description="Test QuestionExtractor on a real job application form",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test on a Greenhouse job posting
  python test_question_extractor.py "https://boards.greenhouse.io/company/jobs/12345"
  
  # Test on a Workday application
  python test_question_extractor.py "https://company.wd1.myworkdayjobs.com/careers/job/position"
  
  # Auto-close browser after 5 seconds
  python test_question_extractor.py "https://example.com/apply" --auto-close
        """
    )
    
    parser.add_argument(
        "url",
        help="URL of the job application form to test"
    )
    
    parser.add_argument(
        "--auto-close",
        action="store_true",
        help="Automatically close the browser after 5 seconds (default: keep open)"
    )
    
    args = parser.parse_args()
    
    try:
        await test_question_extraction_on_url(args.url, keep_open=not args.auto_close)
        logger.info("✅ Test completed successfully")
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Configure logger
    logger.remove()  # Remove default handler
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"  # Changed to INFO for cleaner output
    )
    
    asyncio.run(main())

