import ssl
import certifi
import urllib.request

def test():
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        urllib.request.urlopen("https://livekit.cloud", context=context)
        print("✅ SSL verification successful!")
    except Exception as e:
        print(f"❌ SSL verification failed: {e}")

test()