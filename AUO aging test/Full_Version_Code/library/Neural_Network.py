#!/usr/bin/env python
# coding: utf-8

# In[1]:


import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import pickle
from tqdm.auto import tqdm

import optuna
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, random_split
from torchvision import datasets, transforms
from torch.autograd import Variable
from torch.nn.modules.loss import _WeightedLoss

from library.Data_Preprocessing import Balance_Ratio, train_col
from library.Imbalance_Sampling import label_divide
from library.Aging_Score_Contour import score1
from library.AdaBoost import train_set, multiple_set, multiple_month, line_chart, AUC, PR_curve, multiple_curve,     best_threshold, all_optuna

os.chdir('C:/Users/user/Desktop/Darui_R08621110')  
os.getcwd()


# ## 

# In[2]:


##### GPU ??? #####
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print('Using {} device.'.format(device))


# ### Dataloader

# In[3]:


class RunhistSet(Dataset):
    
    def __init__(self, train_x, train_y):
        self.x = torch.tensor(train_x.values.astype(np.float32))
        self.y = torch.tensor(train_y.values.astype(np.float32)).long()
        
    def __len__(self):
        return len(self.x)

    def __getitem__(self, idx):
        return [self.x[idx], self.y[idx]]


# ### Neural Network

# In[4]:


class NeuralNetworkC(nn.Module):
    
    def __init__(self, dim):
        super(NeuralNetworkC, self).__init__()
        self.stack = nn.Sequential(
            nn.Linear(dim, 64),
            nn.ReLU(),
            nn.Dropout(0.25),
#             nn.Linear(64, 16),
#             nn.ReLU(),
#             nn.Dropout(0.25),
#             nn.Linear(64, 16),
#             nn.ReLU(),
#             nn.Dropout(0.25),
            nn.Linear(64, 2)
        )

    def forward(self, x):
        logits = self.stack(x)
        return logits
    
    
class NeuralNetworkR(nn.Module):
    
    def __init__(self):
        super(NeuralNetworkR, self).__init__()
        self.stack = nn.Sequential(
            nn.Linear(114, 64),
            nn.ReLU(),
            nn.Dropout(0.25),
            nn.Linear(64, 16),
            nn.ReLU(),
            nn.Dropout(0.25),
#             nn.Linear(32, 16),
#             nn.ReLU(),
#             nn.Dropout(0.25),
            nn.Linear(16, 1)
        )

    def forward(self, x):
        logits = self.stack(x)
        return logits


# ### label smoothing

# In[5]:


class LabelSmoothingLoss(nn.Module):
    def __init__(self, classes, smoothing=0.0, dim=-1, weight = None):
        super(LabelSmoothingLoss, self).__init__()
        self.confidence = 1.0 - smoothing
        self.smoothing = smoothing
        self.weight = weight
        self.cls = classes
        self.dim = dim                                                                                                                                                                                                                                                                                                                                                                                                                                              

    def forward(self, pred, target):
        assert 0 <= self.smoothing < 1
        pred = pred.log_softmax(dim=self.dim)

        if self.weight is not None:
            pred = pred * self.weight.unsqueeze(0)   

        with torch.no_grad():
            true_dist = torch.zeros_like(pred)
            true_dist.fill_(self.smoothing / (self.cls - 1))
            true_dist.scatter_(1, target.data.unsqueeze(1), self.confidence)
        return torch.mean(torch.sum(-true_dist * pred, dim=self.dim))


# ### training

# In[6]:


def trainingC(network, trainloader, validloader, optimizer, criterion, epoch, filename, early_stop):
    
    network.train()
    best_model = network
    best_objective = 0
    stop_trigger = 0
    train_loss = []
    valid_loss = []
    
    for i in tqdm(range(epoch)):
        
        total_loss = 0
        TP = 0
        TN = 0
        FP = 0
        FN = 0  
        for x, y in trainloader:
            
            x = x.to(device)
            y = y.to(device)
            optimizer.zero_grad()
            output = network(x)
            loss = criterion(output, y)         
            
            _, predicted = torch.max(output.data, 1)
            TP += torch.dot((predicted == y).to(torch.float32), (y == 1).to(torch.float32)).sum().item()
            TN += torch.dot((predicted == y).to(torch.float32), (y == 0).to(torch.float32)).sum().item()
            FN += torch.dot((predicted != y).to(torch.float32), (y == 1).to(torch.float32)).sum().item()
            FP += torch.dot((predicted != y).to(torch.float32), (y == 0).to(torch.float32)).sum().item()
            total_loss += loss.item()*len(y)
            loss.backward()
            optimizer.step()
        
        train_loss.append(total_loss)
        recall = TP / (TP + FN)
        precision = TP / (TP + FP) if FP != 0 else 0
        aging = (TP + FP) / (TP + TN + FP + FN)
        f1 = 2*(recall*precision) / (recall + precision) if (recall + precision) != 0 else 0
            
        print(f'Epoch {i+1}: Train Loss = {total_loss / (TP + TN + FP + FN)}, Recall = {recall}, Aging Rate = {aging}, Precision = {precision}, f1 = {f1}')

        if ((i+1) % 5 == 0):
            five_loss, valid_objective, _ = testingC(network, validloader, criterion)
            valid_loss.append(five_loss)
            
            if valid_objective > best_objective:
                best_objective = valid_objective
                best_model = network
                torch.save(best_model, f'{filename}_NeuralNetworkC_{epoch}.ckpt')
                print(f'Model in epoch {i+1} is saved.\n')
                stop_trigger = 0
            else:
                stop_trigger += 1
                print('')
                
            if stop_trigger == early_stop:
                print(f'Training Finished at epoch {i+1}.')
                return network, train_loss, valid_loss
            
    return network, train_loss, valid_loss


def PR_matrix(predict):
    
    Y_new = predict.sort_values(['predict', 'truth'], ascending = [False, True]).reset_index(drop = True)
    Y_new.loc[Y_new['truth'] != 1, 'truth'] = 0
    
    matrix = pd.DataFrame(Y_new.groupby('predict').sum()).rename(columns = {'truth': 'Bad_Count'})
    matrix = matrix.sort_index(ascending = False)
    matrix['All_Count'] = Y_new.groupby('predict').count()
    matrix['Class_Prob'] = matrix.index
    
    matrix['TP'] = matrix['Bad_Count'].cumsum()
    matrix['FP'] = matrix['All_Count'].cumsum() - matrix['TP']
    matrix['FN'] = matrix['TP'].values[-1] - matrix['TP']
    matrix['TN'] = matrix['FP'].values[-1] - matrix['FP']
    
    matrix['Precision'] = matrix['TP'] / (matrix['TP'] + matrix['FP'])
    matrix['Recall'] = matrix['TP'] / (matrix['TP'] + matrix['FN'])
    matrix['Aging Rate'] = (matrix['TP'] + matrix['FP']) / (matrix['TP'] + matrix['FP'] + matrix['FN'] + matrix['TN'])
    matrix['Efficiency'] = matrix['Recall'] / matrix['Aging Rate']
    matrix['Score'] = score1(matrix['Recall'], matrix['Aging Rate'])            
    matrix = matrix.drop(columns = ['Bad_Count', 'All_Count']).reset_index(drop = True)
    
    return matrix


def trainingR(network, trainloader, validloader, optimizer, criterion, epoch, filename, early_stop):
    
    network.train()
    best_model = network
    best_objective = 1
    stop_trigger = 0
    train_loss = []
    valid_loss = []
    
    for i in tqdm(range(epoch)):
        
        total_loss = 0
        predict_vector = torch.tensor([0])
        y_vector = torch.tensor([0])
        for x, y in trainloader:
            
            x = x.to(device)
            y = y.type(torch.FloatTensor).to(device)
            y = y.unsqueeze(1)
            optimizer.zero_grad()
            output = network(x)
            loss = criterion(output, y)
            total_loss += loss.item()*len(y)
            loss.backward()
            optimizer.step()    
            predict_vector = torch.cat((predict_vector, output.data[:,0]), axis = 0)
            y_vector = torch.cat((y_vector, y[:,0]), axis = 0)       
        result_df = pd.DataFrame(dict(predict = predict_vector, truth = y_vector))
        pr_matrix = PR_matrix(result_df.iloc[1:, :])
        best_data, best_thres = best_threshold(pr_matrix, target = 'Recall', threshold = 0.7)
        auc = AUC(pr_matrix['Recall'], pr_matrix['Aging Rate'])
        train_loss.append(total_loss)
        
        recall = best_data["Recall"].values
        aging = best_data["Aging Rate"].values
        print(f'Epoch {i+1}: Train Loss = {total_loss}, AUC = {auc}, Recall(0.7) = {recall}, Aging Rate = {aging}')
        
        if ((i+1) % 5 == 0):
            five_loss, valid_auc, _ = testingR(network, validloader, criterion)
            valid_loss.append(five_loss)
            
            if valid_auc < best_objective:
                best_objective = valid_auc
                best_model = network
                torch.save(best_model, f'{filename}_NeuralNetworkR_{epoch}.ckpt')
                print(f'Model in epoch {i+1} is saved.\n')
                stop_trigger = 0
            else:
                stop_trigger += 1
                print('')
                
            if stop_trigger == early_stop:
                print(f'Training Finished at epoch {i+1}.')
                return network, train_loss, valid_loss
      
    return network, train_loss, valid_loss


# ### testing

# In[7]:


def testingC(network, dataloader, criterion):
    
    network.eval()
    total_loss = 0
    TP = 0
    TN = 0
    FP = 0
    FN = 0
    
    for x, y in dataloader:
        
        x = x.to(device)
        y = y.to(device)
        output = network(x)
        loss = criterion(output, y)
        
        _, predicted = torch.max(output.data, 1)
        TP += torch.dot((predicted == y).to(torch.float32), (y == 1).to(torch.float32)).sum().item()
        TN += torch.dot((predicted == y).to(torch.float32), (y == 0).to(torch.float32)).sum().item()
        FN += torch.dot((predicted != y).to(torch.float32), (y == 1).to(torch.float32)).sum().item()
        FP += torch.dot((predicted != y).to(torch.float32), (y == 0).to(torch.float32)).sum().item()
        total_loss += loss.item()*len(y)
        
    recall = TP / (TP + FN) if (TP + FN) != 0 else 0
    aging = (TP + FP) / (TP + TN + FP + FN)
    precision = TP / (TP + FP) if (TP + FP) != 0 else 0
    f1 = 2*(recall*precision) / (recall+precision) if (recall + precision) != 0 else 0
    if aging != 0:
        efficiency = recall / aging
        score = score1(recall, aging)
    else:
        efficiency = 0
        score = 0
        
    print(f'Test Loss = {total_loss / (TP + TN + FP + FN)}, Recall = {recall}, Aging Rate = {aging}, precision = {precision}')
    
    valid_objective = (recall+2*precision)
    table = pd.Series({'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN, 'Precision': precision, 'Recall': recall, 
                       'Aging Rate': aging, 'Efficiency': efficiency, 'f1': f1, 'Score': score})
    table = pd.DataFrame(table).T
    
    return total_loss, valid_objective, table


def testingR(network, dataloader, criterion):
    
    network.eval()   
    total_loss = 0
    predict_vector = torch.tensor([0])
    y_vector = torch.tensor([0])
    for x, y in dataloader:
        
        x = x.to(device)
        y = y.to(device)
        y = y.unsqueeze(1)
        output = network(x)
        loss = criterion(output, y)
        total_loss += loss.item()*len(y)
        
        predict_vector = torch.cat((predict_vector, output.data[:,0]), axis = 0)
        y_vector = torch.cat((y_vector, y[:,0]), axis = 0)
    result_df = pd.DataFrame(dict(predict = predict_vector, truth = y_vector))
    pr_matrix = PR_matrix(result_df.iloc[1:, :])
    best_data, best_thres = best_threshold(pr_matrix, target = 'Recall', threshold = 0.7)
    auc = AUC(pr_matrix['Recall'], pr_matrix['Aging Rate'])
    recall = best_data['Recall'].values[0]
    aging = best_data['Aging Rate'].values[0]
    precision = best_data['Precision'].values[0]
    efficiency = best_data['Efficiency'].values[0]
    score = best_data['Score'].values[0]
    TP = best_data['TP'].values[0]
    FP = best_data['FP'].values[0]
    TN = best_data['TN'].values[0]
    FN = best_data['FN'].values[0]
        
    print(f'\nTest Loss = {total_loss}, Recall = {recall}, Aging Rate = {aging}, Efficiency = {efficiency}')
    
    valid_objective = auc
    table = pd.Series({'TP': TP, 'FP': FP, 'FN': FN, 'TN': TN, 'Precision': precision, 'Recall': recall,
                       'Aging Rate': aging,'Efficiency': efficiency, 'Score': score})
    table = pd.DataFrame(table).T
    
    return total_loss, valid_objective, table


# ### Run all datasets

# In[8]:


def runall_nn(train_x, train_y, test_x, test_y, n_epoch, config, filename, early_stop, mode):
    
    set_name = list(train_x.keys())
    result_table = pd.DataFrame()
    train_dict = {}
    valid_dict = {}
    judge = list(config.keys())[0]
    for num, i in enumerate(tqdm(set_name)):
        print(f'\nStarting training Dataset {num}:')
        
        if isinstance(config[judge], dict) :
            best_config = config[i]
        else :
            best_config = config
        
        # data preparation
        train_ratio = 0.75
        train_data = RunhistSet(train_x[i], train_y[i])
        test_data = RunhistSet(test_x, test_y)
        train_size = int(len(train_data)*train_ratio)
        valid_size = len(train_data) - train_size
        train_data, valid_data = random_split(train_data, [train_size, valid_size])
        train_loader = DataLoader(train_data, batch_size = best_config['batch_size'], shuffle = True)
        valid_loader = DataLoader(valid_data, batch_size = best_config['batch_size'], shuffle = False)
        test_loader = DataLoader(test_data, batch_size = best_config['batch_size'], shuffle = False)
        
        modelC = NeuralNetworkC(dim = train_x[i].shape[1]).to(device)
        optimizerC = torch.optim.Adam(modelC.parameters(), lr = best_config['learning_rate'], 
                                      weight_decay = best_config['weight_decay'])
        criterionC = nn.CrossEntropyLoss(
            weight = torch.tensor([1-best_config['bad_weight'], best_config['bad_weight']])).to(device)
        
        # training
        if mode == 'C':
            done_model, train_loss, valid_loss = trainingC(network = modelC, 
                                                           trainloader = train_loader, 
                                                           validloader = valid_loader, 
                                                           optimizer = optimizerC, 
                                                           criterion = criterionC, 
                                                           epoch = n_epoch, 
                                                           filename = filename, 
                                                           early_stop = early_stop)
        elif mode == 'R':
            pass
        
        train_dict[i] = train_loss
        valid_dict[i] = valid_loss
        
        # testing
        if mode == 'C':
            _, _, table = testingC(done_model, test_loader, criterionC)
        elif mode == 'R':
            pass
        result_table = pd.concat([result_table, table], axis = 0).rename({0: f'dataset {num}'})
    loss_dict = dict(train = train_dict, valid = valid_dict)
        
    return result_table, loss_dict


def loss_plot(train_loss, valid_loss, num_row, num_col):
    
    fig , axes = plt.subplots(num_row, num_col, sharex = False, sharey = False, figsize = (num_row*8 + 1, num_col*6))
    plt.suptitle('Training & Validation Loss Curve', y = 0.94, fontsize = 30)
    for row in range(num_row):
        for col in range(num_col):
            
            index = num_col*row + col
            if index < len(train_loss):
                
                train = train_loss[f'set{index}']
                valid = valid_loss[f'set{index}']
                axes[row, col].plot(range(len(train)), train, 'b-', linewidth = 5, label = 'train')
                axes[row, col].plot(range(4, len(train)+1, 5), valid, 'r-', linewidth = 5, label = 'valid')
                axes[row, col].set_xlabel('Epoch')
                axes[row, col].set_ylabel('Total Loss')
                axes[row, col].set_title(f'dataset {index}')
                axes[row, col].legend(loc = 'upper right', fancybox = True, prop = dict(size = 20))


# ### optuna

# In[9]:


def NeuralNetwork_creator(train_data, mode, num_valid = 3, label = 'GB') :
    
    def objective(trial) :

        param = {
            'batch_size': trial.suggest_int('batch_size', 32, 96, step = 32),
            'learning_rate': trial.suggest_categorical('learning_rate', [1e-2, 1e-3, 1e-4]),
            'weight_decay': trial.suggest_categorical('weight_decay', [1e-2, 1e-3, 1e-4]),
            'bad_weight': trial.suggest_categorical('bad_weight', [0.5, 0.6, 0.7])
        }

        result_list = []
        for i in range(num_valid):
            
            train_x, train_y = label_divide(train_data, None, label, train_only = True)
            train_set = RunhistSet(train_x, train_y)
            train_ratio = 0.75
            train_size = int(len(train_data)*train_ratio)
            valid_size = len(train_data) - train_size
            training_data, validing_data = random_split(train_set, [train_size, valid_size])
            train_loader = DataLoader(training_data, batch_size = param['batch_size'], shuffle = True)
            valid_loader = DataLoader(validing_data, batch_size = param['batch_size'], shuffle = False)

            if mode == 'C':
                model = NeuralNetworkC(dim = train_x.shape[1]).to(device)
                optimizer = torch.optim.Adam(model.parameters(), lr = param['learning_rate'], 
                                             weight_decay = param['weight_decay'])
                criterion = nn.CrossEntropyLoss(
                    weight = torch.tensor([1-param['bad_weight'], param['bad_weight']])).to(device)

                done_modelC, train_lossC, valid_lossC = trainingC(network = model, 
                                                                  trainloader = train_loader, 
                                                                  validloader = train_loader, 
                                                                  optimizer = optimizer, 
                                                                  criterion = criterion, 
                                                                  epoch = 100, 
                                                                  filename = 'tamama',
                                                                  early_stop = 10)
                _, valid_objective, _ = testingC(done_modelC, valid_loader, criterion)
                result_list.append(valid_objective)

            elif mode == 'R':
                pass

        return np.mean(result_list)
    
    return objective

'''
# ## 

# ### loading training & testing data

# In[10]:


### training data ### 
training_month = range(2, 5)

data_dict, trainset_x, trainset_y = multiple_month(training_month, num_set = 10, filename = 'dataset')

print('\nCombined training data:\n')
run_train = multiple_set(num_set = 10)
run_train_x, run_train_y = train_set(run_train, num_set = 10)

### testing data ###
run_test = pd.read_csv('test_runhist.csv').iloc[:, 2:]
run_test_x, run_test_y = label_divide(run_test, None, 'GB', train_only = True)
print('\n', 'Dimension of testing data:', run_test.shape)


# ### For one dataset

# In[ ]:


##### data preparation #####
train_data = RunhistSet(run_train_x['set7'], run_train_y['set7'])
test_data = RunhistSet(run_test_x, run_test_y)
train_ratio = 0.75
train_size = int(len(train_data)*train_ratio)
valid_size = len(train_data) - train_size
train_data, valid_data = random_split(train_data, [train_size, valid_size])

train_loader = DataLoader(train_data, batch_size = 48, shuffle = True)
valid_loader = DataLoader(valid_data, batch_size = 48, shuffle = False)
test_loader = DataLoader(test_data, batch_size = 64, shuffle = False)


# #### classifier

# In[ ]:


##### model preparation #####
# hyperparameter: learning rate, weight decay, weight
modelC = NeuralNetworkC(dim = len(train_data[0][0])).to(device)
optimizerC = torch.optim.Adam(modelC.parameters(), lr = 0.001, weight_decay = 0.01)
criterionC = nn.CrossEntropyLoss(weight = torch.tensor([0.5, 0.5])).to(device)

### label smoothing ###
#criterionC = LabelSmoothingLoss(classes = 2, smoothing = 0.2)

##### training #####
done_modelC, train_lossC, valid_lossC = trainingC(network = modelC, 
                                                  trainloader = train_loader, 
                                                  validloader = valid_loader, 
                                                  optimizer = optimizerC, 
                                                  criterion = criterionC, 
                                                  epoch = 200, 
                                                  filename = 'tamama',
                                                  early_stop = 10)

##### testing #####
_, _, result_tableC = testingC(done_modelC, test_loader, criterionC)


# #### regressor

# In[ ]:


##### model preparation #####
# hyperparameter: learning rate, weight decay, weight
modelR = NeuralNetworkR().to(device)
optimizerR = torch.optim.Adam(modelR.parameters(), lr = 0.001, weight_decay = 0.01)
criterionR = nn.MSELoss().to(device)

##### training #####
done_modelR, train_lossR, valid_lossR = trainingR(network = modelR, 
                                                  trainloader = train_loader, 
                                                  validloader = valid_loader, 
                                                  optimizer = optimizerR, 
                                                  criterion = criterionR, 
                                                  epoch = 150, 
                                                  filename = 'tamama',
                                                  early_stop = 10)

##### testing #####
_, _, result_tableR = testingR(done_modelR, test_loader, criterionR)


# ### search for best hyperparameter

# In[11]:


best_paramC, all_scoreC = all_optuna(num_set = 10, 
                                     all_data = run_train, 
                                     mode = 'C', 
                                     TPE_multi = False, 
                                     n_iter = 20, 
                                     filename = 'runhist_array_m2m4_m5_3criteria_NeuralNetwork', 
                                     creator = NeuralNetwork_creator
                                    )


# ### For multiple datasets

# #### classifier

# In[ ]:


table_setC, loss_dictC = runall_nn(train_x = run_train_x, 
                                   train_y = run_train_y, 
                                   test_x = run_test_x, 
                                   test_y = run_test_y, 
                                   n_epoch = 200, 
                                   config = best_paramC,
                                   filename = 'runhist_array_m2m4_m5_3criteria_NeuralNetworkC', 
                                   early_stop = 10,
                                   mode = 'C')


# In[ ]:


loss_plot(loss_dictC['train'], loss_dictC['valid'], num_row = 4, num_col = 3)
table_setC


# #### regressor

# In[ ]:


runall_modelR = NeuralNetworkR().to(device)
runall_optimizerR = torch.optim.Adam(runall_modelR.parameters(), lr = 0.001, weight_decay = 0.001)
runall_criterionR = nn.MSELoss().to(device)

table_setR, loss_dictR = runall_nn(train_x = run_train_x, 
                                   train_y = run_train_y, 
                                   test_x = run_test_x, 
                                   test_y = run_test_y, 
                                   n_epoch = 150, 
                                   batch_size = 64,
                                   model = runall_modelR,
                                   optimizer = runall_optimizerR, 
                                   criterion = runall_criterionR, 
                                   filename = 'runhist_array_4criteria_NeuralNetworkR', 
                                   train_ratio = 0.75, 
                                   early_stop = 10,
                                   mode = 'R')


# In[ ]:


loss_plot(loss_dictR['train'], loss_dictR['valid'], num_row = 4, num_col = 3)
table_setR


# ### export

# In[ ]:


savedate = '20211207'
TPE_multi = False

table_setC['sampler'] = 'multivariate-TPE' if TPE_multi else 'univariate-TPE'
table_setC['model'] = 'NeuralNetwork'
with pd.ExcelWriter(f'{savedate}_Classifier.xlsx', mode = 'a') as writer:
    table_setC.to_excel(writer, sheet_name = 'NeuralNetwork')
'''
