"""Standard-library-only compatibility policy shared by backup and local upgrade."""
def compatibility(source,target):
 if source==target:return {'compatible':True,'source_version':source,'target_version':target,'migration_required':False,'steps':[],'destructive':False}
 if (target=='0.5.0' and source in {'0.2.0','0.3.0','0.3.1','0.3.2','0.3.3','0.4.0','0.4.1'}) or (target=='0.4.1' and source in {'0.2.0','0.3.0','0.3.1','0.3.2','0.3.3','0.4.0'}) or (target=='0.4.0' and source in {'0.2.0','0.3.0','0.3.1','0.3.2','0.3.3'}) or (source,target) in {('0.2.0','0.3.3'),('0.3.0','0.3.3'),('0.3.1','0.3.3'),('0.3.2','0.3.3'),('0.2.0','0.3.2'),('0.3.0','0.3.2'),('0.3.1','0.3.2'),('0.2.0','0.3.0'),('0.2.0','0.3.1'),('0.3.0','0.3.1')}:
  return {'compatible':True,'source_version':source,'target_version':target,'migration_required':True,'destructive':False,'steps':['保留全部项目、检查点、知识库与费用记录','仅新增缺失的文档取消字段、验收表与智能体调用表，不删除已有字段或记录；旧任务保留原检查点流程','保留一份迁移前原始备份','按入口说明处理密钥与调用许可，费用记录不重置']}
 return {'compatible':False,'source_version':source,'target_version':target,'migration_required':False,'destructive':False,'steps':[],'requires_user_decision':True}

