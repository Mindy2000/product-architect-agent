"""Standard-library-only compatibility policy shared by backup and local upgrade."""
def compatibility(source,target):
 if source==target:return {'compatible':True,'source_version':source,'target_version':target,'migration_required':False,'steps':[],'destructive':False}
 if (source,target)==('0.2.0','0.3.0'):
  return {'compatible':True,'source_version':source,'target_version':target,'migration_required':True,'destructive':False,'steps':['保留全部项目、检查点、知识库与费用记录','新增文档任务取消字段，不删除或改写已有字段','保留一份迁移前原始备份','按入口说明处理密钥与调用许可，费用记录不重置']}
 return {'compatible':False,'source_version':source,'target_version':target,'migration_required':False,'destructive':False,'steps':[],'requires_user_decision':True}

