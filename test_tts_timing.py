#!/usr/bin/env python3
"""
Test script to measure ElevenLabs TTS response times
"""

import os
import time
import statistics
from tts_helper import TTSHelper

def test_tts_timing():
    """Test TTS response times with different text lengths"""
    
    # Test texts of different lengths
    test_texts = [
        ("Short", "Hello, this is a short test message."),
        ("Medium", "This is a medium length test message that should take a bit longer to process. It contains multiple sentences and should give us a good idea of typical response times."),
        ("Long", "This is a longer test message that simulates a typical AI response. It contains multiple paragraphs and should take significantly longer to process. We want to see how the TTS service handles longer content and whether it might be causing timeouts in our web application. This should help us determine if we need to adjust our timeout settings or implement better error handling for TTS operations."),
        ("Very Long", "This is a very long test message that simulates the longest responses we might get from our AI. It contains multiple paragraphs with detailed content that should take the maximum amount of time to process. We want to see if this causes any issues with our current timeout settings and whether we need to adjust our approach to TTS generation. This test will help us understand the upper limits of what we can reasonably expect from the ElevenLabs service and whether we need to implement chunking or other strategies for very long content.")
    ]
    
    # Initialize TTS helper
    tts = TTSHelper()
    
    if not tts.enabled:
        print("❌ TTS not enabled - no API key found")
        return
    
    print(f"🎤 TTS enabled with mode: {tts.mode}")
    print(f"🔍 Testing TTS response times...")
    print("=" * 60)
    
    results = {}
    
    for test_name, test_text in test_texts:
        print(f"\n📝 Testing: {test_name} ({len(test_text)} characters)")
        print(f"Text: {test_text[:100]}...")
        
        times = []
        errors = 0
        
        # Run 3 tests for each text length
        for i in range(3):
            try:
                print(f"  Test {i+1}/3...", end=" ")
                start_time = time.time()
                
                # Test TTS generation (without saving to file)
                audio_file = tts.speak(test_text, save_audio=False)
                
                end_time = time.time()
                duration = end_time - start_time
                times.append(duration)
                
                print(f"✅ {duration:.2f}s")
                
            except Exception as e:
                errors += 1
                print(f"❌ Error: {e}")
        
        if times:
            avg_time = statistics.mean(times)
            min_time = min(times)
            max_time = max(times)
            
            results[test_name] = {
                'avg': avg_time,
                'min': min_time,
                'max': max_time,
                'errors': errors
            }
            
            print(f"  📊 Results: avg={avg_time:.2f}s, min={min_time:.2f}s, max={max_time:.2f}s")
        else:
            print(f"  ❌ All tests failed")
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TTS TIMING SUMMARY")
    print("=" * 60)
    
    for test_name, result in results.items():
        print(f"{test_name:12} | Avg: {result['avg']:6.2f}s | Min: {result['min']:6.2f}s | Max: {result['max']:6.2f}s | Errors: {result['errors']}")
    
    # Recommendations
    print("\n💡 RECOMMENDATIONS:")
    
    max_avg = max([r['avg'] for r in results.values()]) if results else 0
    
    if max_avg > 30:
        print(f"⚠️  Maximum average time is {max_avg:.2f}s - this may cause 502 errors")
        print("   Consider reducing TTS character limits or implementing async processing")
    elif max_avg > 15:
        print(f"⚠️  Maximum average time is {max_avg:.2f}s - this is borderline")
        print("   Monitor for timeout issues")
    else:
        print(f"✅ Maximum average time is {max_avg:.2f}s - this should be fine")

if __name__ == "__main__":
    test_tts_timing()
