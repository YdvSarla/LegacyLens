import urllib2

def fetch(url):
    print "fetching", url
    return urllib2.urlopen(url).read()
