import zc.zk

zk = zc.zk.ZooKeeper()

zk.import_tree("""
/simul
  cache_size=20000
  clients=12
  lambda=0.01
  objects_per_site=1000
  objects_per_request=100
  sites=40
  workers=2
  /lb
    /providers
    /workers
      history=999
      threads=1
      /providers
""", trim=True)
